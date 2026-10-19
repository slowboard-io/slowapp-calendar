"""AI tagging and its cache: a series is asked about once; unchanged events make no AI call."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta

import replay
from calapp import ai, sync

NOW = datetime.fromisoformat(replay.NOW).astimezone(replay.TZ)


def run(r, state, now=NOW, docs=None, key=replay.AI_KEY, **kw):
    tagger = ai.tagger(r, key, replay.AI_BASE) if key else None
    return sync.run(now, replay.sources(r), docs or sync.Docs(), state, tagger=tagger, **kw)


class Cache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def state(self):
        return sync.State(self.dir)

    def test_unchanged_events_make_no_ai_call(self):
        r = replay.Replay()
        st = self.state()
        first = run(r, st)
        st.save()
        self.assertEqual(len(r.ai_calls()), 1)
        self.assertEqual(first.report["got"], 17)
        # The next day's first sync after 05:00 is the daily run again: every series is cached.
        r2 = replay.Replay()
        st2 = self.state()
        second = run(r2, st2, now=NOW + timedelta(days=1), force_ai=True)
        self.assertEqual(r2.ai_calls(), [])
        self.assertEqual(second.report["asked"], 0)
        self.assertEqual([x.get("tag") for x in second.rows if x["date"] >= "2026-10-20"],
                         [x.get("tag") for x in first.rows if x["date"] >= "2026-10-20"])

    def test_only_a_new_or_renamed_series_is_sent(self):
        st = self.state()
        run(replay.Replay(), st)
        # Coffee with Sam is renamed in the calendar: a new hash, so it alone is asked about.
        ev = {s: st.state["events"][s] for s in st.state["events"]}
        for o in ev["google:alex@example.com"]:
            if o["title"] == "Coffee with Sam":
                o["title"] = "Coffee with Sam and Jo"
        tags = {"Coffee with Sam and Jo": ("regular", "social")}
        r = replay.Replay(ai_tags=tags)

        class Stale:                      # the sources return what's in state (no refetch of the fixture)
            def __init__(self, src):
                self.id, self.kind, self.label = src.id, src.kind, src.label

            def fetch(self, t0, t1, tz):
                return {}, ev[self.id]
        res = sync.run(NOW, [Stale(s) for s in replay.sources(r)], sync.Docs(), st,
                       tagger=ai.tagger(r, replay.AI_KEY, replay.AI_BASE), force_ai=True)
        self.assertEqual(len(r.ai_calls()), 1)
        sent = json.loads(json.loads(r.ai_calls()[0]["body"])["messages"][0]["content"].split("DATA:\n", 1)[1])
        asked = [s["title"] for s in sent["month"] if s["id"] in sent["tag_these"]]
        self.assertEqual(asked, ["Coffee with Sam and Jo"])
        self.assertEqual(res.report["got"], 1)

    def test_daily_gate_and_force(self):
        r = replay.Replay()
        early = NOW.replace(hour=4, minute=45)
        res = run(r, self.state(), now=early)
        self.assertEqual(r.ai_calls(), [])                       # before 05:00: rules for now
        self.assertTrue(all(x.get("tag") for x in res.rows if x["cal"] != "holiday"))
        res = run(r, self.state(), now=early, force_ai=True)     # "Tag with AI now"
        self.assertEqual(len(r.ai_calls()), 1)
        st = self.state()
        st.state["ai_day"] = NOW.date().isoformat()              # today's run is done
        run(r, st)
        self.assertEqual(len(r.ai_calls()), 1)

    def test_failure_falls_back_to_rules_and_retries(self):
        st = self.state()
        res = run(replay.Replay(fail={"ai"}), st)
        self.assertIn("AI unavailable, rules for now", res.lines)
        self.assertFalse(st.state["ai"]["ok"])
        self.assertNotIn("ai_day", st.state)
        self.assertEqual(st.cache, {})
        r = replay.Replay()
        run(r, st)                                               # the next sync tries again
        self.assertEqual(len(r.ai_calls()), 1)
        self.assertEqual(st.state["ai_day"], "2026-10-19")

    def test_no_key_means_rules_only(self):
        r = replay.Replay()
        res = run(r, self.state(), key=None)
        self.assertEqual(r.ai_calls(), [])
        self.assertEqual(res.report["asked"], 0)

    def test_user_and_legacy_ai_tags(self):
        team = "google:alex@example.com|teamsync01"
        dentist = "google:alex@example.com|dentist2026@google.com"
        docs = sync.Docs(tags={team: {"tag": "important", "by": "user"},
                               dentist: {"tag": "regular", "by": "ai", "why": "calsync said so"},
                               "google:alex@example.com|coffeesam@google.com": {"tag": "important", "by": "rules"}})
        r = replay.Replay()
        st = self.state()
        res = run(r, st, docs=docs)
        sent = json.loads(json.loads(r.ai_calls()[0]["body"])["messages"][0]["content"].split("DATA:\n", 1)[1])
        asked = {s["title"] for s in sent["month"] if s["id"] in sent["tag_these"]}
        self.assertNotIn("Weekly team sync", asked)              # the household's tag wins
        self.assertNotIn("Dentist", asked)                       # calsync's AI tag seeds the cache
        self.assertIn("Coffee with Sam", asked)                  # a rules tag is only for now
        by_title = {x["title"]: x["tag"] for x in res.rows if x["cal"] != "holiday"}
        self.assertEqual((by_title["Weekly team sync"], by_title["Dentist"]), ("important", "regular"))

    def test_what_is_sent(self):
        r = replay.Replay()
        run(r, self.state())
        call = r.ai_calls()[0]
        body = json.loads(call["body"])
        self.assertEqual(body["model"], "router:general")
        prompt = body["messages"][0]["content"]
        for private in ("Sam's place", "Bring lunch", "sales@vendor.example", replay.ICAL_URL, replay.GOOGLE_TOKEN):
            self.assertNotIn(private, prompt)

    def test_reply_is_checked(self):
        short = {"s0": "a|x", "s1": "a|y"}
        got = ai.parse_reply('Sure! {"s0": {"tag": "important", "why": "<b>big</b>"}, "s1": {"tag": "delete"},'
                             ' "s9": {"tag": "hidden"}}', ["s0", "s1"], short)
        self.assertEqual(got, {"a|x": ("important", "bbig/b")})
        with self.assertRaises(ai.AIError):
            ai.parse_reply("no", ["s0"], short)

    def test_endpoint_and_prune(self):
        self.assertEqual(ai.chat_url("https://api.example.com/v1/"), "https://api.example.com/v1/chat/completions")
        self.assertEqual(ai.chat_url("https://x/v1/chat/completions"), "https://x/v1/chat/completions")
        cache = {"old": dict(tag="hidden", seen="2026-01-01"), "new": dict(tag="hidden", seen="2026-10-01")}
        self.assertEqual(list(ai.prune(cache, NOW.date())), ["new"])


if __name__ == "__main__":
    unittest.main()
