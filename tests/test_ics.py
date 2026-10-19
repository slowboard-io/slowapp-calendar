"""ics.py: parsing and recurrence expansion, stdlib only."""
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from calapp import ics

TZ = ZoneInfo("America/Vancouver")
T0 = datetime(2026, 9, 21, tzinfo=TZ)
T1 = datetime(2026, 11, 3, tzinfo=TZ)


def cal(*events, extra=""):
    body = "".join(f"BEGIN:VEVENT\r\n{e.strip()}\r\nEND:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{extra}{body}END:VCALENDAR\r\n"


def starts(text, t0=T0, t1=T1):
    return [i["start"] for i in ics.instances(ics.parse(text), t0, t1, TZ)]


class Parse(unittest.TestCase):
    def test_folding_escapes_and_params(self):
        c = ics.parse(cal("UID:a\nSUMMARY:Lunch\\, then\n  a walk\nDTSTART;TZID=\"America/Vancouver\":20261020T120000",
                          extra="X-WR-CALNAME:Fam\\, ily\r\n"))
        self.assertEqual(c["name"], "Fam, ily")
        ev = c["events"][0]
        self.assertEqual(ics.unescape(ics.first(ev, "SUMMARY")[1]), "Lunch, then a walk")
        self.assertEqual(ics.first(ev, "DTSTART")[0]["TZID"], "America/Vancouver")

    def test_nested_components_are_not_events(self):
        c = ics.parse(cal("UID:a\nSUMMARY:x\nDTSTART:20261020T120000Z\nBEGIN:VALARM\nTRIGGER:-PT15M\nSUMMARY:alarm\nEND:VALARM"))
        self.assertEqual(len(c["events"]), 1)
        self.assertEqual(ics.first(c["events"][0], "SUMMARY")[1], "x")

    def test_values(self):
        self.assertEqual(ics.when("20261020", {}, TZ), date(2026, 10, 20))
        self.assertEqual(ics.when("20261020T170000Z", {}, TZ), datetime(2026, 10, 20, 17, tzinfo=timezone.utc))
        moz = ics.when("20261020T100000", {"TZID": "/mozilla.org/20050126_1/America/Vancouver"}, timezone.utc)
        self.assertEqual(moz.utcoffset(), timedelta(hours=-7))
        floating = ics.when("20261020T100000", {"TZID": "Pacific Standard Time"}, TZ)  # unknown -> display zone
        self.assertEqual(floating.tzinfo, TZ)
        self.assertEqual(ics.duration("PT1H30M"), timedelta(minutes=90))
        self.assertEqual(ics.duration("P1W"), timedelta(days=7))
        self.assertEqual(ics.duration("-P1D"), timedelta(days=-1))


class Recurrence(unittest.TestCase):
    def test_weekly_with_exdate_and_moved_instance(self):
        text = cal("UID:g\nSUMMARY:Games\nDTSTART;TZID=America/Vancouver:20261001T190000\n"
                   "DTEND;TZID=America/Vancouver:20261001T220000\nRRULE:FREQ=WEEKLY;BYDAY=TH\n"
                   "EXDATE;TZID=America/Vancouver:20261008T190000",
                   "UID:g\nSUMMARY:Games (Fri)\nRECURRENCE-ID;TZID=America/Vancouver:20261015T190000\n"
                   "DTSTART;TZID=America/Vancouver:20261016T190000\nDTEND;TZID=America/Vancouver:20261016T220000")
        got = ics.instances(ics.parse(text), T0, T1, TZ)
        self.assertEqual([(i["start"].day, i["title"]) for i in got],
                         [(1, "Games"), (16, "Games (Fri)"), (22, "Games"), (29, "Games")])
        self.assertTrue(all(i["repeating"] for i in got))

    def test_wall_clock_kept_across_dst(self):
        # 25 Oct 2026 ends summer time in Berlin: a weekly 10:00 stays 10:00 there, its offset moves.
        # (Not Vancouver: tzdata 2026 has British Columbia on permanent daylight time.)
        berlin = ZoneInfo("Europe/Berlin")
        got = ics.rrule_starts(ics.parse_rrule("FREQ=DAILY;INTERVAL=7"), datetime(2026, 10, 19, 10, tzinfo=berlin),
                               datetime(2026, 11, 3, tzinfo=berlin))
        self.assertEqual([(s.day, s.hour, s.utcoffset().total_seconds() / 3600) for s in got], [(19, 10, 2), (26, 10, 1), (2, 10, 1)])

    def test_monthly_last_weekday_and_bysetpos(self):
        self.assertEqual([s.date() for s in starts(cal("UID:b\nDTSTART;TZID=America/Vancouver:20260826T193000\nRRULE:FREQ=MONTHLY;BYDAY=-1WE"))],
                         [date(2026, 9, 30), date(2026, 10, 28)])
        # the last weekday of the month
        self.assertEqual([s.date() for s in starts(cal("UID:p\nDTSTART:20260831T160000Z\nRRULE:FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"))],
                         [date(2026, 9, 30), date(2026, 10, 30)])
        self.assertEqual(starts(cal("UID:m\nDTSTART;VALUE=DATE:20260115\nRRULE:FREQ=MONTHLY;BYMONTHDAY=15,-1")),
                         [date(2026, 9, 30), date(2026, 10, 15), date(2026, 10, 31)])

    def test_count_and_until(self):
        # COUNT counts from DTSTART, before the window: 6 weekly from 1 Sep, so the last is 6 Oct
        self.assertEqual([s.day for s in starts(cal("UID:c\nDTSTART:20260901T170000Z\nRRULE:FREQ=WEEKLY;COUNT=6"))],
                         [22, 29, 6])
        self.assertEqual([s.day for s in starts(cal("UID:u\nDTSTART:20261005T170000Z\nRRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20261011T170000Z"))],
                         [5, 7, 9, 11])
        self.assertEqual(starts(cal("UID:d\nDTSTART;VALUE=DATE:20261018\nRRULE:FREQ=DAILY;UNTIL=20261020")),
                         [date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 20)])

    def test_yearly_and_rdate(self):
        got = starts(cal("UID:y\nDTSTART;VALUE=DATE:19800923\nRRULE:FREQ=YEARLY",
                         "UID:t\nDTSTART;VALUE=DATE:20201012\nRRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=2MO",
                         "UID:r\nDTSTART:20261001T170000Z\nRDATE:20261020T170000Z,20261027T170000Z"))
        self.assertEqual(sorted(s if isinstance(s, date) and not isinstance(s, datetime) else s.date() for s in got),
                         [date(2026, 9, 23), date(2026, 10, 1), date(2026, 10, 12), date(2026, 10, 20), date(2026, 10, 27)])

    def test_unsupported_rule_gives_the_first_instance(self):
        self.assertEqual(len(starts(cal("UID:h\nDTSTART:20261020T170000Z\nRRULE:FREQ=HOURLY;COUNT=5"))), 1)
        self.assertEqual(len(starts(cal("UID:n\nDTSTART:20261020T170000Z\nRRULE:FREQ=YEARLY;BYWEEKNO=43"))), 1)

    def test_cancelled_overlap_and_all_day(self):
        text = cal("UID:x\nSTATUS:CANCELLED\nDTSTART:20261020T170000Z",
                   "UID:o\nDTSTART;TZID=America/Vancouver:20260920T230000\nDTEND;TZID=America/Vancouver:20260921T010000",
                   "UID:a\nDTSTART;VALUE=DATE:20261102\nDTEND;VALUE=DATE:20261104",
                   "UID:late\nDTSTART;VALUE=DATE:20261103")
        got = ics.instances(ics.parse(text), T0, T1, TZ)
        self.assertEqual([i["uid"] for i in got], ["o", "a"])   # overlapping the window's start; ends at its end
        self.assertEqual(got[1]["end"], date(2026, 11, 4))

    def test_occurrences_match_calsync(self):
        o = ics.occurrences(cal("UID:abc\nSUMMARY:Late  dinner\nDTSTART;TZID=America/Vancouver:20261020T200000\n"
                                "DTEND;TZID=America/Vancouver:20261021T010000"),
                            "icloud:a@b.c", "icloud:a@b.c|https://x/home/", T0, T1, TZ)[0]
        self.assertEqual(o, dict(id="icloud:a@b.c|abc|2026-10-20T20:00:00-07:00", series="icloud:a@b.c|abc",
                                 account="icloud:a@b.c", cal_key="icloud:a@b.c|https://x/home/", title="Late dinner",
                                 date="2026-10-20", start="20:00", end="23:59", repeating=False, holiday=False, focus=False))


if __name__ == "__main__":
    unittest.main()
