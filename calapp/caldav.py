"""A small CalDAV client (RFC 4791), enough for iCloud: find the calendars, then ask each for the
events in a time range. Standard library only (calsync.py used the caldav package, which isn't in
the base image).

  principal   PROPFIND / (Depth 0)              current-user-principal
  home        PROPFIND <principal> (Depth 0)    calendar-home-set (iCloud: another host, pNN-caldav)
  calendars   PROPFIND <home> (Depth 1)         displayname, resourcetype, supported components
  events      REPORT <calendar> (Depth 1)       calendar-query, VEVENT in [t0, t1): calendar-data

Repeats are expanded here (ics.py), not by the server. Credentials are an Apple ID and an
app-specific password (account.apple.com, Sign-In and Security), sent as HTTP Basic.
"""
import base64
import json
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timezone

from . import ics
from .http import HTTPError

ICLOUD = "https://caldav.icloud.com/"
D, C = "DAV:", "urn:ietf:params:xml:ns:caldav"
NS = {"d": D, "c": C}

PROPFIND_PRINCIPAL = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>"""
PROPFIND_HOME = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>"""
PROPFIND_CALENDARS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/>
<c:supported-calendar-component-set/></d:prop></d:propfind>"""
REPORT_EVENTS = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/></d:prop>
<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{t0}" end="{t1}"/>
</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"""


class Refused(Exception):
    """The Apple ID or app-specific password was refused (401)."""


def credentials(value):
    """The `icloud` secret -> (apple id, app password). One value, any of:
    {"user": "...", "password": "..."} (JSON), "apple-id:password", or the two on two lines."""
    v = (value or "").strip()
    if v.startswith("{"):
        try:
            d = json.loads(v)
        except ValueError:
            d = {}
        user, pw = d.get("user") or d.get("apple_id") or d.get("username"), d.get("password")
    elif "\n" in v:
        user, _, pw = v.partition("\n")
    else:
        user, _, pw = v.partition(":")
    user, pw = (user or "").strip(), (pw or "").strip()
    if not user or not pw:
        raise ValueError("the iCloud secret is the Apple ID and an app-specific password: apple-id:password")
    return user, pw


def _utc(t):
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class CalDAV:
    def __init__(self, request, user, password, url=ICLOUD, timeout=60):
        self.request, self.url, self.timeout = request, url, timeout
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}

    def _dav(self, method, url, depth, body):
        headers = dict(self._auth, Depth=str(depth), **{"Content-Type": "application/xml; charset=utf-8"})
        try:
            r = self.request(method, url, body, headers, self.timeout)
        except HTTPError as e:
            if e.status == 401:
                raise Refused("the sign-in was refused: check the Apple ID and app-specific password") from None
            raise
        return r.url, ET.fromstring(r.body)

    @staticmethod
    def _responses(base, root):
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", default="", namespaces=NS).strip()
            props = []
            for ps in resp.findall("d:propstat", NS):
                status = ps.findtext("d:status", default="", namespaces=NS).split()
                if status[1:2] in ([], ["200"]):                # a 404 propstat lists what's missing
                    props.append(ps.find("d:prop", NS))
            yield urllib.parse.urljoin(base, href), [p for p in props if p is not None]

    def _href_prop(self, url, body, path):
        base, root = self._dav("PROPFIND", url, 0, body)
        for _, props in self._responses(base, root):
            for p in props:
                h = p.findtext(path, default="", namespaces=NS).strip()
                if h:
                    return urllib.parse.urljoin(base, h)
        raise ValueError(f"CalDAV: no {path.split('/')[0]} at {urllib.parse.urlsplit(url).netloc}")

    def principal(self):
        return self._href_prop(self.url, PROPFIND_PRINCIPAL, "d:current-user-principal/d:href")

    def home(self, principal):
        return self._href_prop(principal, PROPFIND_HOME, "c:calendar-home-set/d:href")

    def calendars(self, home=None):
        """-> [{"url", "name"}]: the calendars under the home that hold events (not reminders)."""
        home = home or self.home(self.principal())
        base, root = self._dav("PROPFIND", home, 1, PROPFIND_CALENDARS)
        out = []
        for url, props in self._responses(base, root):
            for p in props:
                if p.find("d:resourcetype/c:calendar", NS) is None:
                    continue
                comps = [c.get("name") for c in p.findall("c:supported-calendar-component-set/c:comp", NS)]
                if comps and "VEVENT" not in comps:
                    continue
                name = (p.findtext("d:displayname", default="", namespaces=NS) or "").strip()
                out.append(dict(url=url, name=name or url.rstrip("/").rsplit("/", 1)[-1]))
        return out

    def events(self, cal_url, t0, t1):
        """-> [VCALENDAR text] with an event in [t0, t1)."""
        body = REPORT_EVENTS.format(t0=_utc(t0), t1=_utc(t1)).encode()
        base, root = self._dav("REPORT", cal_url, 1, body)
        out = []
        for _, props in self._responses(base, root):
            for p in props:
                data = p.findtext("c:calendar-data", default="", namespaces=NS)
                if data.strip():
                    out.append(data)
        return out


def fetch(request, user, password, account, t0, t1, tz, url=ICLOUD):
    """-> ({cal_key: name}, [occurrence]) for one iCloud account."""
    dav = CalDAV(request, user, password, url)
    cals, occs = {}, []
    for c in dav.calendars():
        key = f"{account}|{c['url']}"
        cals[key] = c["name"]
        holiday = "holiday" in c["name"].lower()
        for text in dav.events(c["url"], t0, t1):
            occs += ics.occurrences(text, account, key, t0, t1, tz, holiday)
    return cals, occs
