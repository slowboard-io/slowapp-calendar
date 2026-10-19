"""Occurrences: one event on one day, the unit every source produces and the agenda is built from.

The dict is calsync.py's, key for key, so series ids (the keys of the `tags` and `names` docs) and
calendar keys (the `shown` doc) carry over from the legacy files:

  id        "<series>|<start iso>"            one occurrence
  series    "<account>|<event series id>"      what tags and display names are kept by
  account   "google:<email>", "icloud:<apple id>", "ical:<hash>"
  cal_key   "<account>|<calendar id or url>"
  title, date (YYYY-MM-DD), start / end ("HH:MM", None all day), repeating, holiday, focus
"""
import re
from datetime import datetime, timedelta

PAST_DAYS, AHEAD_DAYS = 28, 14          # the past month is context for the AI: what repeats


def window(now):
    """The sync window around `now` (aware): midnight PAST_DAYS ago to midnight after AHEAD_DAYS."""
    t0 = (now - timedelta(days=PAST_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    t1 = (now + timedelta(days=AHEAD_DAYS + 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return t0, t1


def clean_title(title):
    return re.sub(r"\s+", " ", title or "").strip()[:200] or "(no title)"


def occurrence(account, cal_key, series, title, start, end, repeating, holiday, focus):
    """start and end are both dates (all day) or both aware datetimes in the display zone."""
    timed = isinstance(start, datetime)
    d0 = start.date() if timed else start
    endt = None
    if timed:
        endt = end.strftime("%H:%M") if end.date() == d0 else "23:59"
    series_id = f"{account}|{series}"
    return dict(id=f"{series_id}|{start.isoformat()}", series=series_id, account=account, cal_key=cal_key,
                title=clean_title(title), date=d0.isoformat(),
                start=start.strftime("%H:%M") if timed else None, end=endt,
                repeating=bool(repeating), holiday=bool(holiday), focus=bool(focus))
