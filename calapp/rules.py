"""The rules score, the stand-in tags until the AI has looked at a series, and the near-duplicate
merge render uses. Copied from epaper-dashboard renderer/events.py (the legacy dashboard's), so the
calendar app tags exactly as calsync.py's rules did:

  +2  one-off      the title doesn't repeat in the window
  -3  repeating    it does (classes, weekly calls, game nights)
  +3  big/social   pitch, panel, conference, launch, drinks, dinner, party, wedding, birthday...
  +2  notable      roundtable, workshop, meetup, D&D / board games, hike...
  +1  long         at least 2.5 h (and not a time block)
  -4  routine      lecture, class, office hours, block, standup, weekly...
  -2  mundane      coffee, 1:1, sync, check-in, call, meeting
  -1  tentative    shown with "(maybe)" instead of the "Tentative:" prefix
  personal         therapy, dentist, doctor... hidden (the wall is semi-public)

A row with a "tag" (hidden / regular / important) skips the score: hidden are dropped, important
are highlighted.
"""
import re
from datetime import datetime

SHOW, HIGHLIGHT = 3, 5

BIG = r"pitch|panel|agm|conference|summit|launch|demo day|drinks|dinner|party|wedding|birthday|" \
      r"concert|show your work|festival|bsides|hackathon|keynote|tribute|gala|reunion"
NOTABLE = r"roundtable|workshop|meetup|mixer|d&d|\bdnd\b|board game|game night|hike|bbq|potluck"
ROUTINE = r"\blecture\b|tutorial|\bclass\b|office hours|^block\b|\bblock$|block for|standup|stand-up|" \
          r"dev meeting|weekly|water plants|lecture times|focus time|commute"
MUNDANE = r"coffee|<>|\b1:1\b|one on one|\bsync\b|check-in|check in|\bcall\b|\bmeeting\b"
TENTATIVE = r"^tentative:?\s*|\bmaybe\b|\?$"
PERSONAL = r"therapy|dentist|doctor|physio|counsel|clinic|optometr|massage"


def _mins(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def score(e):
    """-> (score, reason, personal?)"""
    t = e["title"].lower()
    if re.search(PERSONAL, t):
        return SHOW - 1, "personal (hidden)", True
    s, why = 0, []
    if e["reps"] <= 1:
        s += 2; why.append("one-off")
    else:
        s -= 3; why.append(f"repeats x{e['reps']}")
    for pattern, pts, name in ((BIG, 3, "big/social"), (NOTABLE, 2, "notable"), (ROUTINE, -4, "routine"),
                               (MUNDANE, -2, "mundane"), (TENTATIVE, -1, "tentative")):
        if re.search(pattern, t):
            s += pts; why.append(name)
    if e["start"] and e["end"] and _mins(e["end"]) - _mins(e["start"]) >= 150 and "block" not in t:
        s += 1; why.append("long")
    return s, ", ".join(why), False


def display_title(title):
    """Tidy for the wall: drop room codes and clearance tags, turn 'Tentative:' into '(maybe)'."""
    t = re.sub(r"\s*\((?:unclassified|ecs[^)]*|in-person)\)", "", title, flags=re.I)
    if re.match(r"^tentative:?\s*", t, flags=re.I):
        t = re.sub(r"^tentative:?\s*", "", t, flags=re.I) + " (maybe)"
    return t


def _key(title):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split()[:3])


def classify(events, now):
    """-> (holiday banner titles, [(event, score, reason, highlight)] in time order)."""
    banners, rows = [], []
    for e in events:
        if e["cal"] == "holiday":
            if e["date"] == now.strftime("%Y-%m-%d"):
                banners.append(re.sub(r"\s*\(.*?\)", "", e["title"]))
            continue
        if e["end"] and datetime.fromisoformat(f"{e['date']}T{e['end']}") <= now:
            continue
        if e.get("tag"):
            if e["tag"] == "hidden":
                continue
            s, why, hi = (HIGHLIGHT, "important", True) if e["tag"] == "important" else (SHOW, "regular", False)
        else:
            s, why, personal = score(e)
            if s < SHOW:
                continue
            hi = s >= HIGHLIGHT and not personal
        dup = next((r for r in rows if r[0]["date"] == e["date"] and _key(r[0]["title"]) == _key(e["title"])), None)
        if dup:
            if len(e["title"]) > len(dup[0]["title"]):
                rows[rows.index(dup)] = (e, max(s, dup[1]), why, hi or dup[3])
            continue
        rows.append((e, s, why, hi))
    rows.sort(key=lambda r: (r[0]["date"], r[0]["start"] or ""))
    return banners, rows




TAG_VALUES = ("hidden", "regular", "important")


def rules_tag(o, reps):
    """-> (tag, why) for an occurrence whose series occurs `reps` times in the window."""
    s, why, personal = score(dict(title=o["title"], reps=reps, start=o["start"], end=o["end"]))
    if personal or s < SHOW:
        return "hidden", why
    return ("important" if s >= HIGHLIGHT else "regular"), why
