"""sync.py and agenda.py: the whole sync on the saved responses, and the agenda's shape.

The default example's artifact, fixtures/default/artifacts/agenda/agenda.json, is this sync's
output at the example's time with fixtures/sources/docs.json applied, so the fixture render draws
exactly what a sync of the saved responses gives. After changing the sources or the app's logic,
rewrite it with:  UPDATE_FIXTURES=1 python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from datetime import datetime

import replay
from calapp import ai, rules, sync

NOW = datetime.fromisoformat(replay.NOW).astimezone(replay.TZ)
GOLDEN = replay.ROOT / "fixtures" / "default" / "artifacts" / "agenda" / "agenda.json"
LEGACY_KEYS = {"date", "title", "start", "end", "cal", "reps"}


def docs():
    return sync.Docs(**json.loads((replay.SOURCES / "docs.json").read_text()))


def fixture_sync(r=None, state=None, d=None, **kw):
    r = r or replay.Replay()
    st = state or sync.State(tempfile.mkdtemp())
    return sync.run(NOW, replay.sources(r), d or docs(), st, tagger=ai.tagger(r, replay.AI_KEY, replay.AI_BASE), **kw), st, r


class Golden(unittest.TestCase):
    def test_fixture_artifact_is_the_sync_of_the_saved_responses(self):
        res, _, _ = fixture_sync()
        text = json.dumps(res.rows, indent=1, ensure_ascii=False) + "\n"
        if os.environ.get("UPDATE_FIXTURES"):
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(text)
        self.assertEqual(json.loads(GOLDEN.read_text()), res.rows,
                         "fixtures/default/artifacts/agenda/agenda.json is stale: UPDATE_FIXTURES=1")

    def test_shape_is_calsyncs(self):
        res, _, _ = fixture_sync()
        for row in res.rows:
            if row["cal"] == "holiday":
                self.assertEqual(set(row), LEGACY_KEYS)
            else:
                self.assertEqual(set(row), LEGACY_KEYS | {"tag"})
                self.assertIn(row["tag"], rules.TAG_VALUES)
        # what the legacy renderer (and weather's copy of it) does with it
        banners, picked = rules.classify(res.rows, datetime(2026, 10, 31, 8, 0))
        self.assertEqual(banners, ["Halloween"])
        self.assertNotIn("Water plants", [e["title"] for e, *_ in picked])
        self.assertTrue(next(hi for e, _, _, hi in picked if e["title"] == "Jo & Kim's wedding"))

    def test_docs_applied(self):
        res, _, _ = fixture_sync()
        by_title = {x["title"]: x for x in res.rows}
        self.assertIn("Pitch night at the Tectoria", by_title)                  # names
        self.assertNotIn("Startup pitch night", by_title)
        self.assertEqual(by_title["Demo day dry run"]["tag"], "important")       # the household's tag

    def test_shown_leaves_calendars_off(self):
        d = docs()
        d.shown = {"google:alex@example.com|family0123@group.calendar.google.com": False,
                   "google:alex@example.com|en.canadian#holiday@group.v.calendar.google.com": {"show": False}}
        res, _, _ = fixture_sync(d=d)
        titles = {x["title"] for x in res.rows}
        self.assertNotIn("Soccer practice", titles)
        self.assertNotIn("Halloween", titles)
        self.assertIn("Mum's birthday", titles)       # still there from the iCal address, which is shown


class Failures(unittest.TestCase):
    def test_a_failed_account_keeps_its_last_good_copy(self):
        res, st, _ = fixture_sync()
        before = [x for x in res.rows]
        res2, st, _ = fixture_sync(r=replay.Replay(fail={"icloud"}), state=st)
        self.assertFalse(res2.ok)
        self.assertEqual(res2.rows, before)
        status = st.state["status"]["icloud:alex@icloud.com"]
        self.assertFalse(status["ok"])
        self.assertIn("app-specific password", status["error"])
        self.assertEqual(status["events"], 16)                              # from the last good sync
        self.assertTrue(any(line.startswith("failed: alex@icloud.com") for line in res2.lines))

    def test_a_disconnected_account_drops_out(self):
        _, st, _ = fixture_sync()
        r = replay.Replay()
        sync.run(NOW, replay.sources(r, icloud=False), docs(), st)
        self.assertNotIn("icloud:alex@icloud.com", st.state["events"])
        self.assertFalse(any(k.startswith("icloud:") for k in st.state["calendars"]))

    def test_a_doc_change_rebuilds_without_the_network(self):
        _, st, _ = fixture_sync()
        st.save()
        st = sync.State(st.root)
        r = replay.Replay()
        d = docs()
        d.names["icloud:alex@icloud.com|91B7E0D2-HIKE-0002"] = "East Sooke"
        res = sync.run(NOW, [], d, st, tagger=None, refetch=False)
        self.assertEqual(r.calls, [])
        self.assertIn("East Sooke", {x["title"] for x in res.rows})
        self.assertEqual(sum(1 for x in res.rows if x.get("tag") == "important"), 5)   # the AI's four and the household's one

    def test_state_round_trips(self):
        _, st, _ = fixture_sync()
        st.save()
        again = sync.State(st.root)
        self.assertEqual(again.state, json.loads(json.dumps(st.state)))
        self.assertEqual(len(again.cache), 16)                    # 17 series, one tagged by the household


class Calendars(unittest.TestCase):
    def test_picker_lists_every_account(self):
        r = replay.Replay(fail={"ical"})
        d = {"google:alex@example.com|family0123@group.calendar.google.com": False}
        got = sync.calendars(replay.sources(r), d)
        self.assertEqual([(a["id"].split(":")[0], a["ok"]) for a in got], [("google", True), ("icloud", True), ("ical", False)])
        g = got[0]["calendars"]
        self.assertEqual([(c["name"], c["shown"]) for c in g], [("Alex", True), ("Family", False), ("Holidays in Canada", True)])
        self.assertEqual([c["name"] for c in got[1]["calendars"]], ["Home"])
        self.assertNotIn("private-0123abcd", json.dumps(got))       # the secret address isn't in the result


if __name__ == "__main__":
    unittest.main()
