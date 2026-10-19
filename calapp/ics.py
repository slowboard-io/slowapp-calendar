"""iCalendar (RFC 5545) text -> occurrences, with recurrences expanded. Standard library only, so it
runs in the bare base image and its tests run in CI as they are.

Used for iCloud (CalDAV hands back VCALENDAR objects) and for a secret iCal address (one big
VCALENDAR). It covers what calendar apps write for events:

  DTSTART / DTEND / DURATION    DATE, floating, UTC (Z) and TZID times (TZID as an IANA name; a
                                Mozilla-style "/.../America/Vancouver" path is trimmed to it)
  RRULE                         FREQ DAILY / WEEKLY / MONTHLY / YEARLY with INTERVAL, COUNT, UNTIL,
                                BYDAY (with ordinals: 2TU, -1FR), BYMONTHDAY, BYMONTH, BYSETPOS, WKST
  RDATE, EXDATE                 extra and excluded instances
  RECURRENCE-ID                 a changed (or cancelled) instance replaces the one it names
  STATUS:CANCELLED              skipped

Not covered (a series using them shows its first instance only): BYWEEKNO, BYYEARDAY, BYHOUR and
finer, and FREQ below DAILY. VTIMEZONE blocks are not read; the TZID's IANA name is used, and an
unknown TZID falls back to the display zone.
"""
import calendar as _cal
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .occ import occurrence

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MAX_PERIODS = 20000          # a daily series from 1970 is ~20 000 days; anything past that is a bug


# ------------------------------------------------------------------ parsing

def unfold(text):
    out = []
    for line in re.split(r"\r\n|\n|\r", text):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def parse_line(line):
    """'DTSTART;TZID=America/Vancouver:20261019T100000' -> ("DTSTART", {"TZID": ...}, "2026...")."""
    quoted, colon = False, None
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            colon = i
            break
    if colon is None:
        return None
    head, value = line[:colon], line[colon + 1:]
    parts, cur, quoted = [], "", False
    for ch in head:
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, value


def unescape(v):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def parse(text):
    """-> {"name": X-WR-CALNAME or None, "events": [{PROP: [(params, value), ...]}]}."""
    cal = {"name": None, "events": []}
    stack, cur = [], None
    for line in unfold(text):
        p = parse_line(line)
        if not p:
            continue
        name, params, value = p
        if name == "BEGIN":
            stack.append(value.upper())
            if value.upper() == "VEVENT" and len(stack) >= 2 and stack[-2] == "VCALENDAR":
                cur = {}
        elif name == "END":
            if stack and stack[-1] == "VEVENT" and cur is not None and stack[-2:-1] == ["VCALENDAR"]:
                cal["events"].append(cur)
                cur = None
            if stack:
                stack.pop()
        elif cur is not None and stack and stack[-1] == "VEVENT":
            cur.setdefault(name, []).append((params, value))
        elif stack == ["VCALENDAR"] and name == "X-WR-CALNAME":
            cal["name"] = unescape(value)
    return cal


def first(ev, name, default=None):
    v = ev.get(name)
    return v[0] if v else default


# ------------------------------------------------------------------ values

def zone(tzid, fallback):
    tzid = (tzid or "").strip().strip('"')
    if not tzid:
        return fallback
    if tzid.upper() in ("UTC", "GMT", "Z", "ETC/UTC"):
        return timezone.utc
    candidates = [tzid]
    bits = [b for b in tzid.split("/") if b]
    if len(bits) >= 2:
        candidates += ["/".join(bits[-2:]), "/".join(bits[-3:])]
    for c in candidates:
        try:
            return ZoneInfo(c)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return fallback


def when(value, params, fallback_tz):
    """One DATE or DATE-TIME value -> date, or an aware datetime (floating times in fallback_tz)."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v[:8], "%Y%m%d").date()
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc)
    return dt.replace(tzinfo=zone(params.get("TZID"), fallback_tz))


def whens(prop_list, fallback_tz):
    """Every value of a multi-valued property (EXDATE, RDATE): comma-separated, possibly repeated."""
    out = []
    for params, value in prop_list or []:
        if params.get("VALUE", "").upper() == "PERIOD":
            continue
        for v in value.split(","):
            if v.strip():
                out.append(when(v, params, fallback_tz))
    return out


def duration(v):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def instant_key(v):
    """What RECURRENCE-ID and EXDATE are matched on: the instant (or the day, all day)."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


# ------------------------------------------------------------------ RRULE

def parse_rrule(v):
    rule = {}
    for part in v.split(";"):
        k, _, val = part.partition("=")
        if k:
            rule[k.upper()] = val.upper() if k.upper() != "UNTIL" else val
    return rule


def _byday(v):
    out = []
    for item in filter(None, (v or "").split(",")):
        m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", item.strip())
        if m:
            out.append((int(m.group(1)) if m.group(1) else None, WEEKDAYS[m.group(2)]))
    return out


def _ints(v):
    return [int(x) for x in (v or "").split(",") if re.fullmatch(r"[+-]?\d+", x.strip())]


def _nth(days, ordinal):
    """The ordinal-th of `days` (1 first, -1 last), or None."""
    i = ordinal - 1 if ordinal > 0 else ordinal
    return days[i] if -len(days) <= i < len(days) else None


def _month_days(y, m, byday, bymonthday, default_day):
    n = _cal.monthrange(y, m)[1]
    if not byday and not bymonthday:
        return [date(y, m, default_day)] if default_day <= n else []
    md = set()
    for x in bymonthday:
        d = x if x > 0 else n + x + 1
        if 1 <= d <= n:
            md.add(date(y, m, d))
    wd = set()
    for ordinal, w in byday:
        days = [date(y, m, d) for d in range(1, n + 1) if date(y, m, d).weekday() == w]
        if ordinal:
            pick = _nth(days, ordinal)
            if pick:
                wd.add(pick)
        else:
            wd.update(days)
    if byday and bymonthday:
        return sorted(md & wd)
    return sorted(md | wd)


def _period(freq, d0, k, rule):
    """-> (first day of the k-th period, candidate dates in it), before BYSETPOS."""
    byday, bymonthday, bymonth = _byday(rule.get("BYDAY")), _ints(rule.get("BYMONTHDAY")), _ints(rule.get("BYMONTH"))
    if freq == "DAILY":
        d = d0 + timedelta(days=k)
        ok = (not bymonth or d.month in bymonth) and (not byday or d.weekday() in {w for _, w in byday})
        if bymonthday:
            n = _cal.monthrange(d.year, d.month)[1]
            ok = ok and any(d.day == (x if x > 0 else n + x + 1) for x in bymonthday)
        return d, [d] if ok else []
    if freq == "WEEKLY":
        wkst = WEEKDAYS.get(rule.get("WKST", "MO"), 0)
        ws = d0 - timedelta(days=(d0.weekday() - wkst) % 7) + timedelta(weeks=k)
        wds = [w for _, w in byday] or [d0.weekday()]
        days = sorted(ws + timedelta(days=(w - wkst) % 7) for w in set(wds))
        return ws, [d for d in days if not bymonth or d.month in bymonth]
    if freq == "MONTHLY":
        y, m = divmod(d0.month - 1 + k, 12)
        y, m = d0.year + y, m + 1
        start = date(y, m, 1)
        if bymonth and m not in bymonth:
            return start, []
        return start, _month_days(y, m, byday, bymonthday, d0.day)
    if freq == "YEARLY":
        y = d0.year + k
        start = date(y, 1, 1)
        if byday and not bymonth and not bymonthday:          # e.g. BYDAY=20MO: weekdays of the year
            out = set()
            for ordinal, w in byday:
                days = [start + timedelta(days=i) for i in range(366 if _cal.isleap(y) else 365)]
                days = [d for d in days if d.weekday() == w]
                if ordinal:
                    pick = _nth(days, ordinal)
                    if pick:
                        out.add(pick)
                else:
                    out.update(days)
            return start, sorted(out)
        months = bymonth or (list(range(1, 13)) if bymonthday and not byday else [d0.month])
        days = []
        for m in months:
            days += _month_days(y, m, byday, bymonthday, d0.day)
        return start, sorted(days)
    return None, None


def rrule_starts(rule, dtstart, limit):
    """Instance starts of RRULE `rule` from dtstart (counted, as COUNT counts from it) up to `limit`
    (an aware datetime). dtstart is a date or an aware datetime; instances keep its wall-clock time
    in its zone, across DST changes."""
    freq = rule.get("FREQ")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") or \
            any(k in rule for k in ("BYWEEKNO", "BYYEARDAY", "BYHOUR", "BYMINUTE", "BYSECOND")):
        return [dtstart]
    interval = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    timed = isinstance(dtstart, datetime)
    until = None
    if rule.get("UNTIL"):
        until = when(rule["UNTIL"], {}, dtstart.tzinfo if timed else None)
        if timed and not isinstance(until, datetime):
            until = datetime.combine(until, time(23, 59, 59), dtstart.tzinfo)
        elif not timed and isinstance(until, datetime):
            until = until.date()
    setpos = _ints(rule.get("BYSETPOS"))
    d0 = dtstart.date() if timed else dtstart
    limit_d = limit.date() if isinstance(limit, datetime) else limit
    out, seen, n = [dtstart], {dtstart}, 1
    if count is not None and count <= 1:
        return out
    for k in range(0, MAX_PERIODS):
        pstart, days = _period(freq, d0, k * interval, rule)
        if pstart is None or pstart > limit_d:
            break
        if setpos and days:
            days = sorted({p for p in (_nth(days, x) for x in setpos) if p})
        for d in days:
            if d < d0:
                continue
            s = datetime.combine(d, dtstart.timetz()) if timed else d
            if s in seen:
                continue
            if until is not None and s > until:
                return out
            if (s > limit) if timed else (s > limit_d):
                return out
            out.append(s)
            seen.add(s)
            n += 1
            if count is not None and n >= count:
                return out
    return out


# ------------------------------------------------------------------ instances

def _span(ev, tz):
    """(dtstart, length) of an event: DTEND - DTSTART, else DURATION, else a day (all day) or 0."""
    p, v = first(ev, "DTSTART")
    start = when(v, p, tz)
    if first(ev, "DTEND"):
        ep, evv = first(ev, "DTEND")
        end = when(evv, ep, tz)
        if isinstance(start, datetime) != isinstance(end, datetime):
            end = start + timedelta(days=1) if not isinstance(start, datetime) else start
        return start, end - start
    if first(ev, "DURATION"):
        return start, duration(first(ev, "DURATION")[1])
    return start, timedelta(days=1) if not isinstance(start, datetime) else timedelta(0)


def _overlaps(start, end, t0, t1):
    if isinstance(start, datetime):
        return start < t1 and (end > t0 or (end == start and start >= t0))
    return start < t1.date() and max(end, start + timedelta(days=1)) > t0.date()


def _text(ev, name, default=""):
    v = first(ev, name)
    return unescape(v[1]) if v else default


def instances(cal, t0, t1, tz):
    """Every instance of every event in [t0, t1): dicts uid, title, start, end (dates, or aware
    datetimes in tz), repeating."""
    groups = {}
    for ev in cal["events"]:
        if not first(ev, "DTSTART"):
            continue
        uid = _text(ev, "UID") or f"nouid-{len(groups)}"
        groups.setdefault(uid, {"master": None, "overrides": {}})
        rid = first(ev, "RECURRENCE-ID")
        if rid:
            groups[uid]["overrides"][instant_key(when(rid[1], rid[0], tz))] = ev
        else:
            groups[uid]["master"] = ev
    out = []

    def emit(uid, ev, start, length, repeating):
        if _text(ev, "STATUS").upper() == "CANCELLED":
            return
        end = start + length
        if isinstance(start, datetime):
            start, end = start.astimezone(tz), end.astimezone(tz)
        if _overlaps(start, end, t0, t1):
            out.append(dict(uid=uid, title=_text(ev, "SUMMARY", "(no title)"), start=start, end=end, repeating=repeating))

    for uid, g in groups.items():
        master, overrides = g["master"], g["overrides"]
        repeating = bool(overrides) or bool(master and first(master, "RRULE"))
        if master:
            start, length = _span(master, tz)
            starts = [start]
            if first(master, "RRULE"):
                starts = rrule_starts(parse_rrule(first(master, "RRULE")[1]), start, t1)
            for r in whens(master.get("RDATE"), tz):
                if type(r) is type(start) and r not in starts:
                    starts.append(r)
            ex = {instant_key(x) for x in whens(master.get("EXDATE"), tz)}
            for s in starts:
                k = instant_key(s)
                if k in ex or k in overrides:
                    continue
                emit(uid, master, s, length, repeating)
        for ev in overrides.values():
            s, length = _span(ev, tz)
            emit(uid, ev, s, length, repeating)
    out.sort(key=lambda i: (i["start"].isoformat() if isinstance(i["start"], datetime) else i["start"].isoformat() + "T00"))
    return out


def occurrences(cal, account, cal_key, t0, t1, tz, holiday=False):
    """One VCALENDAR's events (its text, or parse()'s result) as occurrences; series = the UID, as
    calsync's iCloud sync had it."""
    cal = parse(cal) if isinstance(cal, str) else cal
    return [occurrence(account, cal_key, i["uid"], i["title"], i["start"], i["end"], i["repeating"], holiday, False)
            for i in instances(cal, t0, t1, tz)]
