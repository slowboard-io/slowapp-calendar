"""One Source per connected account. platform.py builds them from the run's secrets; tests build them
with a fake `request`. A source knows its account id (the prefix of its series and calendar keys),
how to list its calendars, and how to fetch its occurrences:

  google:<email>      a Google account (oauth secret `google`, one per account)
  icloud:<apple id>   the iCloud account (value secret `icloud`)
  ical:<hash>         a secret iCal address (value secret `ical_url`, one address per line); the
                      address itself is a credential, so the id is a hash of it, never the address
"""
import hashlib
from dataclasses import dataclass
from typing import Callable

from . import caldav, google, ics


@dataclass
class Source:
    id: str
    kind: str                  # google | icloud | ical
    label: str
    calendars: Callable        # () -> {cal_key: name}
    fetch: Callable            # (t0, t1, tz) -> ({cal_key: name}, [occurrence])


def google_source(request, access_token, account):
    """account: the Google address, as the hub names the connected account (S4)."""
    aid = f"google:{account}"

    def calendars():
        return {f"{aid}|{c['id']}": c["name"] for c in google.calendars(request, access_token)}

    def fetch(t0, t1, tz):
        return google.fetch(request, access_token, aid, t0, t1, tz)
    return Source(aid, "google", account, calendars, fetch)


def icloud_source(request, secret_value, url=caldav.ICLOUD):
    user, password = caldav.credentials(secret_value)
    aid = f"icloud:{user}"

    def calendars():
        return {f"{aid}|{c['url']}": c["name"] for c in caldav.CalDAV(request, user, password, url).calendars()}

    def fetch(t0, t1, tz):
        return caldav.fetch(request, user, password, aid, t0, t1, tz, url)
    return Source(aid, "icloud", user, calendars, fetch)


def ical_sources(request, secret_value):
    """One source per address in the `ical_url` secret (one per line). webcal:// is https://."""
    out = []
    for line in (secret_value or "").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        out.append(_ical_source(request, url))
    return out


def _ical_source(request, url):
    aid = "ical:" + hashlib.sha256(url.encode()).hexdigest()[:12]
    key = f"{aid}|feed"
    holiday = "#holiday@" in url

    def _get():
        return ics.parse(request("GET", url, None, {"Accept": "text/calendar"}, 60).text())

    def name(cal):
        return cal["name"] or "iCal address"

    def calendars():
        return {key: name(_get())}

    def fetch(t0, t1, tz):
        cal = _get()
        hol = holiday or "holiday" in name(cal).lower()
        return {key: name(cal)}, ics.occurrences(cal, aid, key, t0, t1, tz, hol)
    return Source(aid, "ical", "iCal address", calendars, fetch)
