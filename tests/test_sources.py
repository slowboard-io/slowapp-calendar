"""google.py, caldav.py, sources.py, merge.py against the saved responses in fixtures/sources/."""
import unittest
from datetime import datetime

from calapp import caldav, google, merge, sources
from calapp.http import HTTPError
from calapp.occ import window

import replay

NOW = datetime.fromisoformat(replay.NOW).astimezone(replay.TZ)
T0, T1 = window(NOW)


class Google(unittest.TestCase):
    def test_calendars_and_email(self):
        cals = google.calendars(replay.Replay(), replay.GOOGLE_TOKEN)
        self.assertEqual([c["name"] for c in cals], ["Alex", "Family", "Holidays in Canada"])
        self.assertEqual(google.email(cals), "alex@example.com")
        self.assertEqual([c["holiday"] for c in cals], [False, False, True])

    def test_fetch_skips_what_calsync_skipped(self):
        r = replay.Replay()
        cals, occs = google.fetch(r, replay.GOOGLE_TOKEN, "google:alex@example.com", T0, T1, replay.TZ)
        titles = {o["title"] for o in occs}
        for gone in ("Vendor demo", "Old planning meeting", "Home"):           # declined, cancelled, working location
            self.assertNotIn(gone, titles)
        self.assertIn("Demo day dry run", titles)                             # on the second page
        self.assertEqual(r.count("pageToken=page2token"), 1)
        self.assertEqual(cals["google:alex@example.com|alex@example.com"], "Alex")
        sync = [o for o in occs if o["title"] == "Weekly team sync"]
        self.assertEqual(len(sync), 7)
        self.assertTrue(all(o["repeating"] and o["series"] == "google:alex@example.com|teamsync01" for o in sync))
        flight = next(o for o in occs if o["title"].startswith("Flight"))
        self.assertEqual((flight["start"], flight["end"]), ("07:15", "12:40"))   # Toronto end shown in Vancouver time
        self.assertTrue(next(o for o in occs if o["title"] == "Focus time")["focus"])
        self.assertTrue(all(o["holiday"] for o in occs if o["title"] in ("Thanksgiving", "Halloween")))
        bday = next(o for o in occs if o["title"] == "Mum's birthday")
        self.assertEqual((bday["date"], bday["start"], bday["end"]), ("2026-10-23", None, None))

    def test_a_refused_token_is_an_error_for_the_account(self):
        with self.assertRaises(HTTPError) as e:
            google.calendars(replay.Replay(fail={"google"}), replay.GOOGLE_TOKEN)
        self.assertEqual(e.exception.status, 401)


class ICloud(unittest.TestCase):
    def test_credentials_forms(self):
        for v in ("a@icloud.com:pw-1", "a@icloud.com\npw-1\n", '{"user": "a@icloud.com", "password": "pw-1"}'):
            self.assertEqual(caldav.credentials(v), ("a@icloud.com", "pw-1"))
        for bad in ("", "a@icloud.com", '{"user": "x"}'):
            with self.assertRaises(ValueError):
                caldav.credentials(bad)

    def test_discovery_and_events(self):
        r = replay.Replay()
        dav = caldav.CalDAV(r, "alex@icloud.com", "pw")
        cals = dav.calendars()
        self.assertEqual(cals, [dict(url="https://p42-caldav.icloud.com:443/123456789/calendars/home/", name="Home")])
        self.assertEqual([c["method"] for c in r.calls], ["PROPFIND", "PROPFIND", "PROPFIND"])
        src = sources.icloud_source(r, replay.ICLOUD_SECRET)
        got_cals, occs = src.fetch(T0, T1, replay.TZ)
        self.assertEqual(list(got_cals.values()), ["Home"])
        report = [c for c in r.calls if c["method"] == "REPORT"][-1]
        self.assertIn(b'start="20260921T070000Z" end="20261103T070000Z"', report["body"])
        self.assertEqual(sorted({o["title"] for o in occs}),
                         ["Board game night", "Book club", "Hike: East Sooke loop", "Jo & Kim's wedding", "Physio", "Water plants"])
        self.assertEqual(src.id, "icloud:alex@icloud.com")

    def test_refused_password(self):
        with self.assertRaises(caldav.Refused):
            caldav.CalDAV(replay.Replay(fail={"icloud"}), "a", "b").calendars()


class ICal(unittest.TestCase):
    def test_address_is_a_hashed_id_and_never_logged(self):
        srcs = sources.ical_sources(replay.Replay(), "# comment\n\nwebcal://example.com/a.ics\n" + replay.ICAL_URL)
        self.assertEqual(len(srcs), 2)
        self.assertTrue(all(s.id.startswith("ical:") and "private" not in s.id for s in srcs))
        cals, occs = srcs[1].fetch(T0, T1, replay.TZ)
        self.assertEqual(list(cals.values()), ["Family"])
        self.assertIn("School pickup swap with the Nguyens", {o["title"] for o in occs})


class Merge(unittest.TestCase):
    def test_duplicates_across_accounts_keep_the_first_source(self):
        r = replay.Replay()
        events = {s.id: s.fetch(T0, T1, replay.TZ)[1] for s in replay.sources(r)}
        merged = merge.flatten(merge.merge(events))
        bdays = [o for o in merged if o["title"] == "Mum's birthday"]
        self.assertEqual([o["account"] for o in bdays], ["google:alex@example.com"])
        self.assertEqual(len({o["id"] for o in merged}), len(merged))

    def test_same_event_on_two_calendars_of_one_account(self):
        o = dict(id="a|s|1", date="2026-10-20", start="10:00", end="11:00", title="Lunch")
        out = merge.merge({"google:a": [dict(o, cal_key="google:a|c1"), dict(o, cal_key="google:a|c2")]})
        self.assertEqual([x["cal_key"] for x in out["google:a"]], ["google:a|c1"])


if __name__ == "__main__":
    unittest.main()
