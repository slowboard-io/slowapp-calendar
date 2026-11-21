"""AI tagging: each event series gets hidden, regular or important from the household's own AI key,
through a decision model: the month goes in as shared context and each series is one choice
question over the three tags, answered with calibrated probabilities. The model writes no text, so
an answer can only be one of the three tags (as epaper-dashboard tools/calsync.py now asks).

Two providers speak it: TypeSafe's Jev on DigitalOcean's System One API (the default) and Google's
Gemini decisions API. They differ only in the URL, the key's header and the field the shared month
goes in (Jev: `state`, a JSON object; Gemini: `input`, the same as JSON text).

Only titles, times, calendar names and repeat counts are sent: no descriptions, places or
attendees. Titles from other people's invitations are untrusted; they go in as data, a question's
answer can only be one of the three tags, and the reply is still checked: known ids and real tags.

The cache. A series is sent once: its tag is kept under a hash of what the AI was shown about it
(series_hash: the series id, title, calendar name, start time and whether it repeats). A series
whose hash is in the cache is never sent again, so a sync with nothing new makes no AI call. A
renamed event (a new title) has a new hash and is asked about again. Entries not seen for
CACHE_DAYS are dropped.
"""
import hashlib
import json
import re
from datetime import date

from .occ import AHEAD_DAYS, PAST_DAYS
from .rules import TAG_VALUES

PROVIDERS = {
    "jev": dict(base_url="https://inference.do-ai.run/v1", path="/systemone", model="typesafe-jev-1.13.0",
                header="Authorization", prefix="Bearer ", context="state"),
    "gemini": dict(base_url="https://generativelanguage.googleapis.com/v1beta", path="/decisions",
                   model="gemini-decision-1", header="x-goog-api-key", prefix="", context="input"),
}
DEFAULT_PROVIDER = "jev"
WORKDAY = (8, 18)                 # Mon-Fri, for "inside the workday"
BATCH = 60                        # series per request
TIMEOUT_S = 120
CACHE_DAYS = 90

INSTRUCTIONS = """The state is every event series in a home's calendars over a rolling window (the last {past} days and
the next {ahead}), for a wall display seen by guests. "weekly" and "times_in_window" say what repeats; the
workday is Mon-Fri, {w0}:00-{w1}:00. How should the series with id "{sid}" (titled {title}) be shown on the wall?"""

CRITERIA = {
    "hidden": "Routine that doesn't need a spot on the wall. Mainly work things that recur (weekly or more often) "
              "inside the workday: standups, syncs, 1:1s, team meetings, office hours, lectures, classes. Also focus "
              "or block time, commutes, reminders like 'water plants', and private medical or therapy appointments.",
    "regular": "Worth a line on the agenda: one-off meetings, social plans, and recurring things outside the workday "
               "such as a weekly game night or a sports league.",
    "important": "Worth highlighting: one-off occasions (pitches, talks, conferences, launches, parties, "
                 "weddings, birthdays, concerts, trips and flights, deadlines, interviews), and anything unusual and big. "
                 "Always includes bin days: garbage, recycling, green bin, compost or yard waste pickup, however spelled.",
}


class AIError(Exception):
    pass


def series_hash(o, cal_name):
    """What the AI is shown about a series, hashed: the cache key."""
    key = [o["series"], o["title"], cal_name or "", o["start"] or "all day", bool(o["repeating"])]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:32]


def series_summary(occs, cal_names):
    """Compact per-series context: what, where, when, how often in the window. {series: {...}}."""
    by = {}
    for o in occs:
        by.setdefault(o["series"], []).append(o)
    out = {}
    for i, (sid, os_) in enumerate(sorted(by.items(), key=lambda kv: (min(o["date"] for o in kv[1]), kv[0]))):
        os_.sort(key=lambda o: (o["date"], o["start"] or ""))
        days = sorted({date.fromisoformat(o["date"]).strftime("%a") for o in os_})
        first = os_[0]
        clean = re.sub(r"[\x00-\x1f\x7f]", " ", first["title"])[:80]
        dur = None
        if first["start"] and first["end"]:
            dur = (int(first["end"][:2]) * 60 + int(first["end"][3:])) - (int(first["start"][:2]) * 60 + int(first["start"][3:]))
        out[sid] = dict(id=f"s{i}", title=clean, calendar=cal_names.get(first["cal_key"], "")[:40],
                        weekdays=days, time=first["start"] or "all day", minutes=dur, times_in_window=len(os_),
                        weekly=first["repeating"] or len(os_) >= 3, dates=[o["date"] for o in os_][:6])
    return out


def provider_of(name):
    return PROVIDERS.get((name or DEFAULT_PROVIDER).strip().lower()) or PROVIDERS[DEFAULT_PROVIDER]


def endpoint(provider, base_url=None):
    """The decisions URL: the provider's path on its base, or on `base_url` (a proxy or region)."""
    p = provider_of(provider)
    base = (base_url or p["base_url"]).rstrip("/")
    return base if base.endswith(p["path"]) else base + p["path"]


def questions_for(want, summary):
    """{short id: choice question} for the series in `want`."""
    out = {}
    for sid in want:
        s = summary.get(sid)
        if s:
            out[s["id"]] = dict(type="choice", criteria=CRITERIA, instructions=INSTRUCTIONS.format(
                past=PAST_DAYS, ahead=AHEAD_DAYS, w0=WORKDAY[0], w1=WORKDAY[1], sid=s["id"],
                title=json.dumps(s["title"], ensure_ascii=False)))
    return out


def tagger(request, key, provider=DEFAULT_PROVIDER, base_url=None, model=None, batch=BATCH, timeout=TIMEOUT_S):
    """-> tag(want, summary) -> ({series: (tag, why)}, "model, tokens"). Raises AIError."""
    p = provider_of(provider)
    url = endpoint(provider, base_url)

    def tag(want, summary):
        short = {v["id"]: sid for sid, v in summary.items()}
        state = dict(month=list(summary.values()))
        context = json.dumps(state, ensure_ascii=False) if p["context"] == "input" else state
        result, models, tin = {}, set(), 0
        for i in range(0, len(want), batch):
            qs = questions_for(want[i:i + batch], summary)
            if not qs:
                continue
            body = json.dumps({"model": model or p["model"], p["context"]: context, "questions": qs}).encode()
            try:
                resp = request("POST", url, body, {p["header"]: p["prefix"] + key,
                                                   "Content-Type": "application/json"}, timeout).json()
            except ValueError as e:
                raise AIError(f"AI reply wasn't JSON: {e}") from None
            except Exception as e:                  # noqa: BLE001 - the HTTP client's errors, shown in status
                raise AIError(str(e)) from None
            if not isinstance(resp, dict):
                raise AIError("AI reply wasn't a JSON object")
            models.add(str(resp.get("model") or model or p["model"]))
            usage = resp.get("usage") or resp.get("usageMetadata") or {}
            tin += usage.get("input_tokens") or usage.get("promptTokenCount") or 0
            result.update(parse_answers(resp, qs, short))
        return result, f"{', '.join(sorted(models))}, {tin:,} in tokens"
    return tag


def parse_answers(resp, questions, short):
    """The decision reply -> {series: (tag, why)}, keeping only questions it was asked and real tags.
    The why is the answer's probability and the model's confidence."""
    answers = resp.get("answers")
    if not isinstance(answers, dict):
        raise AIError("AI reply has no answers")
    out = {}
    for qid, v in answers.items():
        if qid in questions and isinstance(v, dict) and v.get("choice") in TAG_VALUES:
            try:
                prob = float((v.get("probabilities") or {}).get(v["choice"], 0))
                conf = float(v.get("confidence", 0))
            except (TypeError, ValueError):
                prob, conf = 0.0, 0.0
            out[short[qid]] = (v["choice"], f"{prob:.0%} {v['choice']}, confidence {conf:.2f}")
    return out


def prune(cache, today, days=CACHE_DAYS):
    """Drop cache entries not seen for `days` (today: a date)."""
    cutoff = date.fromordinal(today.toordinal() - days).isoformat()
    return {h: v for h, v in cache.items() if v.get("seen", v.get("at", "")) >= cutoff}
