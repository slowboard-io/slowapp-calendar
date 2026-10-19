"""Fakes for the tests: an HTTP client that answers from fixtures/sources/, and an AI endpoint that
tags by title. Every request is recorded, so tests can count calls (no AI call when nothing changed)
and check what was sent (the Bearer token, Basic auth, no secrets in URLs).

The fixture world, at 2026-10-19 09:00 America/Vancouver (a Monday):
  google:alex@example.com   calendars Alex (primary, two pages), Family, Holidays in Canada
  icloud:alex@icloud.com    calendars Home (VEVENT) and Reminders (VTODO, skipped)
  ical:<hash>               a secret iCal address of the Family calendar (its birthday is a duplicate)
"""
import json
import re
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from calapp.http import HTTPError, Response

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "fixtures" / "sources"
TZ = ZoneInfo("America/Vancouver")
NOW = "2026-10-19T09:00:00-07:00"

GOOGLE_TOKEN = "fixture-google-access-token"
ICLOUD_SECRET = "alex@icloud.com:abcd-efgh-ijkl-mnop"
ICAL_URL = "https://calendar.google.com/calendar/ical/family0123%40group.calendar.google.com/private-0123abcd/basic.ics"
AI_KEY = "fixture-ai-key"
AI_BASE = "https://inference.do-ai.run/v1"

GOOGLE_FILES = {
    "alex@example.com": "google-events-primary",
    "family0123@group.calendar.google.com": "google-events-family",
    "en.canadian#holiday@group.v.calendar.google.com": "google-events-holidays",
}

# What the fake AI answers, by title: the tags a sensible model gives these.
AI_TAGS = {
    "Weekly team sync": ("hidden", "weekly work meeting"),
    "1:1 Alex / Priya": ("hidden", "recurring work 1:1"),
    "Dentist": ("hidden", "private medical"),
    "Focus time": ("hidden", "focus block"),
    "Coffee with Sam": ("regular", "one-off social"),
    "Startup pitch night": ("important", "pitch event"),
    "Flight to Toronto (AC 112)": ("important", "travel"),
    "Demo day dry run": ("regular", "one-off work prep"),
    "Mum's birthday": ("important", "family birthday"),
    "Soccer practice": ("regular", "weekly evening sport"),
    "Board game night": ("regular", "weekly evening social"),
    "Hike: East Sooke loop": ("regular", "weekend outing"),
    "Water plants": ("hidden", "household reminder"),
    "Book club": ("regular", "monthly evening social"),
    "Physio": ("hidden", "private medical"),
    "Jo & Kim's wedding": ("important", "wedding"),
    "School pickup swap with the Nguyens": ("regular", "one-off family plan"),
}


class Replay:
    def __init__(self, ai_tags=None, fail=()):
        self.calls = []
        self.ai_tags = AI_TAGS if ai_tags is None else ai_tags
        self.fail = set(fail)               # "google", "icloud", "ical", "ai": answer 500 / 401

    def count(self, host_part):
        return sum(1 for c in self.calls if host_part in c["url"])

    def __call__(self, method, url, body=None, headers=None, timeout=30):
        headers = headers or {}
        self.calls.append(dict(method=method, url=url, body=body, headers=headers))
        u = urllib.parse.urlsplit(url)
        if u.netloc == "www.googleapis.com":
            if "google" in self.fail:
                raise HTTPError(401, url, "invalid_token")
            assert headers.get("Authorization") == f"Bearer {GOOGLE_TOKEN}", headers
            return self.google(u)
        if u.netloc.endswith("caldav.icloud.com") or u.netloc.startswith("p42-caldav"):
            if "icloud" in self.fail:
                raise HTTPError(401, url, "")
            assert headers.get("Authorization", "").startswith("Basic "), "CalDAV without Basic auth"
            return self.caldav(method, url, u, headers)
        if url == ICAL_URL:
            if "ical" in self.fail:
                raise HTTPError(500, url, "")
            return Response(200, url, (SOURCES / "ical-family.ics").read_bytes())
        if url == AI_BASE + "/chat/completions":
            if "ai" in self.fail:
                raise HTTPError(503, url, "busy")
            assert headers.get("Authorization") == f"Bearer {AI_KEY}"
            return self.ai(json.loads(body))
        raise AssertionError(f"unexpected request {method} {url}")

    # ---------------------------------------------------------------- Google

    def google(self, u):
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/calendar/v3/users/me/calendarList":
            return Response(200, u.geturl(), (SOURCES / "google-calendarList.json").read_bytes())
        m = re.fullmatch(r"/calendar/v3/calendars/([^/]+)/events", u.path)
        assert m, u.path
        assert q["singleEvents"] == ["true"] and "timeMin" in q and "timeMax" in q
        name = GOOGLE_FILES[urllib.parse.unquote(m.group(1))]
        if q.get("pageToken") == ["page2token"]:
            name += "-page2"
        return Response(200, u.geturl(), (SOURCES / f"{name}.json").read_bytes())

    # ---------------------------------------------------------------- CalDAV

    def caldav(self, method, url, u, headers):
        if method == "PROPFIND" and u.path == "/" and u.netloc == "caldav.icloud.com":
            assert headers.get("Depth") == "0"
            return Response(207, url, (SOURCES / "caldav-principal.xml").read_bytes())
        if method == "PROPFIND" and u.path == "/123456789/principal/":
            return Response(207, url, (SOURCES / "caldav-home.xml").read_bytes())
        if method == "PROPFIND" and u.path == "/123456789/calendars/":
            assert headers.get("Depth") == "1"
            return Response(207, url, (SOURCES / "caldav-calendars.xml").read_bytes())
        if method == "REPORT" and u.path == "/123456789/calendars/home/":
            ics = (SOURCES / "icloud-home.ics").read_text()
            body = ('<?xml version="1.0" encoding="UTF-8"?>\n<multistatus xmlns="DAV:" '
                    'xmlns:C="urn:ietf:params:xml:ns:caldav"><response><href>/123456789/calendars/home/all.ics</href>'
                    f'<propstat><prop><C:calendar-data>{escape(ics)}</C:calendar-data></prop>'
                    '<status>HTTP/1.1 200 OK</status></propstat></response></multistatus>')
            return Response(207, url, body.encode())
        raise AssertionError(f"unexpected CalDAV {method} {url}")

    # ---------------------------------------------------------------- AI

    def ai(self, req):
        prompt = req["messages"][0]["content"]
        data = json.loads(prompt.split("DATA:\n", 1)[1])
        by_id = {s["id"]: s for s in data["month"]}
        reply = {}
        for sid in data["tag_these"]:
            title = by_id[sid]["title"]
            if title in self.ai_tags:
                tag, why = self.ai_tags[title]
                reply[sid] = {"tag": tag, "why": why}
        body = {"model": "fixture-model", "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
                "choices": [{"message": {"role": "assistant", "content": json.dumps(reply)}}]}
        return Response(200, AI_BASE, json.dumps(body).encode())

    def ai_calls(self):
        return [c for c in self.calls if c["url"].startswith(AI_BASE)]


def sources(replay, google=True, icloud=True, ical=True):
    from calapp import sources as s
    out = []
    if google:
        out.append(s.google_source(replay, GOOGLE_TOKEN, "alex@example.com"))
    if icloud:
        out.append(s.icloud_source(replay, ICLOUD_SECRET))
    if ical:
        out += s.ical_sources(replay, ICAL_URL + "\n")
    return out
