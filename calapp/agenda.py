"""Tags, display names and shown calendars applied: the agenda rows, in calsync.py's agenda.json shape
(what epaper-dashboard renderer/events.py reads, and weather imports as calendar:agenda):

  {"date": "YYYY-MM-DD", "title", "start": "HH:MM" | null, "end": "HH:MM" | null,
   "cal": "holiday" | "calendar", "reps": n, "tag": "hidden" | "regular" | "important"}

Holiday rows come first (every holiday in the window, no tag), then the upcoming events (today on)
in time order. The docs, as the app's UI will write them (calsync's files, S7 copies them over):

  tags    {series: {"tag", "by", "why", "at"}}   by "user" (or no by): the household's choice, which
          always wins. by "ai": a tag calsync's AI made, taken as if cached. by "rules": ignored.
  names   {series: "display name"}               the wall's name for a series; the calendar keeps its own
  shown   {cal_key: false | true | {"show": b}}  calendars left off; a calendar not listed is shown
"""
from . import ai as ai_mod
from .rules import TAG_VALUES, rules_tag


def is_shown(shown, cal_key):
    v = (shown or {}).get(cal_key, True)
    return bool(v.get("show", True)) if isinstance(v, dict) else bool(v)


def visible(occs, shown):
    return [o for o in occs if is_shown(shown, o["cal_key"])]


def repeats(occs):
    reps = {}
    for o in occs:
        reps[o["series"]] = reps.get(o["series"], 0) + 1
    return reps


def upcoming(occs, today):
    """today: "YYYY-MM-DD". Occurrences from today on, holidays aside."""
    return [o for o in occs if o["date"] >= today and not o["holiday"]]


def tag(occs, today, user_tags, cache, cal_names, tagger=None):
    """Tag every upcoming series. -> (tags {series: {tag, by, why}}, report).

    Order: the household's tag; else the AI cache (by series_hash); else a calsync AI tag in the
    `tags` doc (seeds the cache); else, if `tagger` is given, the AI is asked about the rest in one
    go and its answers cached; else the rules, for now (not cached: the AI gets them next time).
    `cache` is updated in place. report: {"asked", "got", "error", "used"}."""
    reps = repeats(occs)
    firsts = {}
    for o in sorted(upcoming(occs, today), key=lambda o: (o["date"], o["start"] or "")):
        firsts.setdefault(o["series"], o)
    tags, want, hashes = {}, [], {}
    for sid, o in firsts.items():
        u = (user_tags or {}).get(sid) or {}
        if u.get("tag") in TAG_VALUES and u.get("by", "user") == "user":
            tags[sid] = dict(tag=u["tag"], by="user", why=u.get("why", "set by you"))
            continue
        h = hashes[sid] = ai_mod.series_hash(o, cal_names.get(o["cal_key"], ""))
        if h in cache:
            cache[h]["seen"] = today
            tags[sid] = dict(tag=cache[h]["tag"], by="ai", why=cache[h].get("why", ""))
        elif u.get("tag") in TAG_VALUES and u.get("by") == "ai":
            cache[h] = dict(tag=u["tag"], why=u.get("why", ""), at=u.get("at", today), seen=today)
            tags[sid] = dict(tag=u["tag"], by="ai", why=u.get("why", ""))
        else:
            want.append(sid)
    report = dict(asked=0, got=0, error=None, used=None)
    if want and tagger:
        report["asked"] = len(want)
        try:
            got, report["used"] = tagger(want, ai_mod.series_summary([o for o in occs if not o["holiday"]], cal_names))
        except ai_mod.AIError as e:
            report["error"] = str(e)[:300]
            got = {}
        for sid, (t, why) in got.items():
            cache[hashes[sid]] = dict(tag=t, why=why, at=today, seen=today)
            tags[sid] = dict(tag=t, by="ai", why=why)
        report["got"] = len(got)
    for sid in want:
        if sid not in tags:
            t, why = rules_tag(firsts[sid], reps.get(sid, 1))
            tags[sid] = dict(tag=t, by="rules", why=why)
    return tags, report


def build(occs, tags, names, today):
    """-> agenda rows (see the module docstring). occs: the shown occurrences."""
    names = names or {}
    reps = repeats(occs)

    def title(o):
        n = names.get(o["series"])
        return n if isinstance(n, str) and n.strip() else o["title"]
    rows = [dict(date=o["date"], title=title(o), start=o["start"], end=o["end"], cal="holiday", reps=1)
            for o in sorted((o for o in occs if o["holiday"]), key=lambda o: (o["date"], o["title"]))]
    for o in sorted(upcoming(occs, today), key=lambda o: (o["date"], o["start"] or "", o["title"])):
        rows.append(dict(date=o["date"], title=title(o), start=o["start"], end=o["end"], cal="calendar",
                         reps=reps.get(o["series"], 1), tag=tags.get(o["series"], {}).get("tag", "regular")))
    return rows
