"""Google Calendar API v3, given an access token (the hub refreshes it; the app never sees a refresh
token or the OAuth client). Read-only: calendarList and events.list.

Events come from events.list with singleEvents=true, so Google expands repeats. Skipped, as in
calsync.py: cancelled events, working-location and birthday entries, and invitations this account
declined. A calendar whose id has "#holiday@" (Google's public holiday calendars) is a holiday
calendar: its events become the agenda's `cal: "holiday"` rows.
"""
import urllib.parse
from datetime import date, datetime

from .http import get_json
from .occ import occurrence

API = "https://www.googleapis.com/calendar/v3"
SKIP_TYPES = ("workingLocation", "birthday")


def _auth(access_token):
    return {"Authorization": f"Bearer {access_token}"}


def calendars(request, access_token):
    """-> [{"id", "name", "primary", "holiday"}]: the calendars this account can read."""
    out, page = [], None
    while True:
        q = {"minAccessRole": "reader"}
        if page:
            q["pageToken"] = page
        r = get_json(request, f"{API}/users/me/calendarList?{urllib.parse.urlencode(q)}", _auth(access_token))
        for c in r.get("items", []):
            if c.get("deleted"):
                continue
            out.append(dict(id=c["id"], name=c.get("summaryOverride") or c.get("summary") or c["id"],
                            primary=bool(c.get("primary")), holiday="#holiday@" in c["id"]))
        page = r.get("nextPageToken")
        if not page:
            return out


def email(cals):
    """The account's address: its primary calendar's id (what calsync took from the id_token)."""
    return next((c["id"] for c in cals if c["primary"]), None)


def events(request, access_token, cal_id, t0, t1):
    """Every event instance of one calendar overlapping [t0, t1), raw from the API."""
    out, page = [], None
    while True:
        q = dict(singleEvents="true", orderBy="startTime", maxResults=2500,
                 timeMin=t0.isoformat(), timeMax=t1.isoformat())
        if page:
            q["pageToken"] = page
        url = f"{API}/calendars/{urllib.parse.quote(cal_id, safe='')}/events?{urllib.parse.urlencode(q)}"
        r = get_json(request, url, _auth(access_token))
        out += r.get("items", [])
        page = r.get("nextPageToken")
        if not page:
            return out


def _when(v, tz):
    return datetime.fromisoformat(v["dateTime"]).astimezone(tz) if "dateTime" in v else date.fromisoformat(v["date"])


def occurrences(account, cal_key, items, holiday, tz):
    out = []
    for e in items:
        if e.get("status") == "cancelled" or e.get("eventType") in SKIP_TYPES:
            continue
        if any(a.get("self") and a.get("responseStatus") == "declined" for a in e.get("attendees", [])):
            continue
        if "start" not in e or "end" not in e:
            continue
        start, end = _when(e["start"], tz), _when(e["end"], tz)
        series = e.get("recurringEventId") or e.get("iCalUID") or e["id"]
        out.append(occurrence(account, cal_key, series, e.get("summary"), start, end,
                              bool(e.get("recurringEventId")), holiday, e.get("eventType") == "focusTime"))
    return out


def fetch(request, access_token, account, t0, t1, tz):
    """-> ({cal_key: name}, [occurrence]) for one Google account, ids prefixed with `account`."""
    cals, occs = {}, []
    for c in calendars(request, access_token):
        key = f"{account}|{c['id']}"
        cals[key] = c["name"]
        occs += occurrences(account, key, events(request, access_token, c["id"], t0, t1), c["holiday"], tz)
    return cals, occs
