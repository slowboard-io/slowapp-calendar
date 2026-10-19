"""One sync, as plain code: fetch every account, tag new series, build the agenda. The `sync` job is
this plus the hub's inputs and outputs (platform.py); tests call it with fake sources.

State lives in the sync job's /data (per install on the hub), two JSON files:

  state.json      {"events": {account: [occurrence]}  the last good copy per account
                   "calendars": {cal_key: {"name", "account"}},
                   "status": {account: {"kind", "label", "ok", "at", "events" | "error", "failed_at"}},
                   "ai_day": "YYYY-MM-DD", "ai": {"at", "ok", "detail"}, "last": {"at", "ok", "summary"}}
  ai_cache.json   {series hash: {"tag", "why", "at", "seen"}}

An account that fails keeps its last good copy, and the rest of the sync goes ahead. An account
that is no longer connected drops out, with its calendars.

The AI is asked once a day, at the first sync from `ai_hour` (05:00, before the panel's active
hours), or when the household asks ("Tag with AI now", the job's `ai` argument), and only about
series whose hash it hasn't seen. Until then a new series gets the rules' tag.
"""
import json
import os
from dataclasses import dataclass, field

from . import agenda, ai
from .merge import flatten, merge
from .occ import window


@dataclass
class Docs:
    tags: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    shown: dict = field(default_factory=dict)


@dataclass
class Result:
    rows: list
    ok: bool
    lines: list
    report: dict


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _save(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


class State:
    def __init__(self, root):
        self.root = str(root)
        self.state = _load(os.path.join(self.root, "state.json"), {})
        self.cache = _load(os.path.join(self.root, "ai_cache.json"), {})
        for k in ("events", "calendars", "status"):
            self.state.setdefault(k, {})

    def save(self):
        _save(os.path.join(self.root, "state.json"), self.state)
        _save(os.path.join(self.root, "ai_cache.json"), self.cache)


def fetch_all(sources, state, now):
    """Fetch every source into state; -> [problem]."""
    t0, t1 = window(now)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    events, cals, status = {}, state.state["calendars"], state.state["status"]
    problems = []
    for src in sources:
        try:
            got_cals, occs = src.fetch(t0, t1, now.tzinfo)
            events[src.id] = occs
            for k, name in got_cals.items():
                cals[k] = dict(name=name, account=src.id)
            status[src.id] = dict(kind=src.kind, label=src.label, ok=True, at=stamp, events=len(occs))
        except Exception as e:                      # noqa: BLE001 - one account's failure is shown, the rest goes on
            events[src.id] = state.state["events"].get(src.id, [])
            status[src.id] = dict(status.get(src.id, {}), kind=src.kind, label=src.label, ok=False,
                                  error=str(e)[:300], failed_at=stamp)
            problems.append(f"{src.label}: {str(e)[:120]}")
    ids = {s.id for s in sources}
    state.state["events"] = events
    state.state["calendars"] = {k: v for k, v in cals.items() if v.get("account") in ids}
    state.state["status"] = {k: v for k, v in status.items() if k in ids}
    return problems


def run(now, sources, docs, state, tagger=None, ai_hour=5, force_ai=False, refetch=True):
    """One sync at `now` (aware, in the display zone). refetch=False rebuilds from the last good
    copies without the network or the AI (a doc changed). -> Result."""
    today = now.date().isoformat()
    problems = fetch_all(sources, state, now) if refetch else []
    # Hidden calendars first, then duplicates: an event on a hidden calendar and on a shown one stays.
    occs = flatten(merge({a: agenda.visible(o, docs.shown) for a, o in state.state["events"].items()}))
    cal_names = {k: v.get("name", "") for k, v in state.state["calendars"].items()}
    due = bool(tagger) and refetch and (force_ai or (now.hour >= ai_hour and state.state.get("ai_day") != today))
    tags, report = agenda.tag(occs, today, docs.tags, state.cache, cal_names, tagger if due else None)
    if report["asked"]:
        ok = not report["error"]
        if ok:
            state.state["ai_day"] = today           # a failure tries again next sync
        state.state["ai"] = dict(at=now.strftime("%a %H:%M"), ok=ok,
                                 detail=report["error"] or f"tagged {report['got']} of {report['asked']} new · {report['used']}")
    state.cache = ai.prune(state.cache, now.date())
    rows = agenda.build(occs, tags, docs.names, today)
    shown = [r for r in rows if r["cal"] != "holiday"]
    lines = [f"{len(shown)} upcoming, {sum(1 for r in shown if r.get('tag') == 'hidden')} hidden, "
             f"{sum(1 for r in shown if r.get('tag') == 'important')} important"]
    if report["asked"]:
        lines.append("AI unavailable, rules for now" if report["error"] else f"AI tagged {report['got']} new")
    lines += [f"failed: {p}" for p in problems]
    if refetch:
        state.state["last"] = dict(at=now.strftime("%H:%M"), ok=not problems, summary="; ".join(lines)[:400])
    return Result(rows, not problems, lines, report)


def calendars(sources, shown):
    """For the UI's picker: [{"id", "kind", "label", "ok", "error"?, "calendars": [{key, name, shown}]}]."""
    out = []
    for src in sources:
        entry = dict(id=src.id, kind=src.kind, label=src.label, ok=True, calendars=[])
        try:
            cals = src.calendars()
            entry["calendars"] = [dict(key=k, name=n, shown=agenda.is_shown(shown, k))
                                  for k, n in sorted(cals.items(), key=lambda kv: kv[1].casefold())]
        except Exception as e:                      # noqa: BLE001 - shown next to the account
            entry.update(ok=False, error=str(e)[:300])
        out.append(entry)
    return out
