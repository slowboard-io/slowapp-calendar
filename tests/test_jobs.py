"""The jobs and the contract 6 adapter (platform.py), with a stand-in for the kit's contract 6
context, so they run on today's base (1.4.1) as well as on 1.5.0. Render runs on the real kit."""
import json
import os
import shutil
import tempfile
import tomllib
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import replay
from calapp import draw, jobs, platform
from slowapp_kit import Context, NeedsConfig, Surface, check_inks

ROOT = replay.ROOT
NOW = datetime.fromisoformat(replay.NOW).astimezone(replay.TZ)
MANIFEST = tomllib.loads((ROOT / "slowapp.toml").read_text())


class SyncCtx:
    """What a contract 6 sync run sees: tokens, value secrets, docs, args, artifacts, /data."""

    def __init__(self, data, google=True, icloud=True, ical=True, ai=True, reason="schedule", args=None, docs=None):
        self.data, self.now, self.reason, self.fixture = Path(data), NOW, reason, False
        self.args = args or {}
        self.settings = {k: v["default"] for k, v in MANIFEST["settings"].items()}
        self.secrets = {"icloud": replay.ICLOUD_SECRET if icloud else None,
                        "ical_url": replay.ICAL_URL if ical else None,
                        "ai_key": replay.AI_KEY if ai else None}
        self.google = google
        self._docs = docs or {}
        self.artifacts, self.summary, self.res = {}, None, None

    def token(self, name):
        assert name == "google"
        if not self.google:
            raise NeedsConfig("secret 'google': no account is connected")
        return [{"access_token": replay.GOOGLE_TOKEN, "expires_at": "2026-10-19T10:00:00-07:00",
                 "scopes": MANIFEST["auth"]["google"]["scopes"], "account": "alex@example.com"}]

    def secret(self, name, optional=False):
        assert name in MANIFEST["secrets"] and MANIFEST["secrets"][name].get("kind", "value") == "value"
        return self.secrets[name]

    def doc(self, name, default=None):
        return self._docs.get(name, default)

    def remaining(self):
        return 100.0

    def write_artifact(self, name, file, value):
        assert name in MANIFEST["artifacts"]
        self.artifacts[(name, file)] = json.loads(json.dumps(value))

    def read_artifact(self, name, file, default=None):
        return self.artifacts.get((name, file), default)

    def write_summary(self, status, lines, actions):
        self.summary = dict(status=status, lines=lines, actions=actions)

    def result(self, value):
        self.res = value


class Sync(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.replay = replay.Replay()
        p = mock.patch.object(platform.http, "urllib_request", self.replay)
        p.start()
        self.addCleanup(p.stop)

    def test_sync_writes_the_agenda_artifact_and_state(self):
        ctx = SyncCtx(self.data, docs=json.loads((replay.SOURCES / "docs.json").read_text()))
        jobs.sync_job(ctx)
        golden = json.loads((ROOT / "fixtures/default/artifacts/agenda/agenda.json").read_text())
        self.assertEqual(ctx.artifacts[("agenda", "agenda.json")], golden)
        self.assertEqual(ctx.summary["status"], "ok")
        self.assertTrue((Path(self.data) / "state.json").is_file() and (Path(self.data) / "ai_cache.json").is_file())
        self.assertEqual(len(self.replay.ai_calls()), 1)
        jobs.sync_job(SyncCtx(self.data))                          # 15 minutes later: nothing new for the AI
        self.assertEqual(len(self.replay.ai_calls()), 1)

    def test_secrets_never_reach_state_or_the_artifact(self):
        ctx = SyncCtx(self.data)
        jobs.sync_job(ctx)
        written = json.dumps(list(ctx.artifacts.values())) + "".join(p.read_text() for p in Path(self.data).glob("*.json"))
        for secret in (replay.GOOGLE_TOKEN, "abcd-efgh-ijkl-mnop", "private-0123abcd", replay.AI_KEY):
            self.assertNotIn(secret, written)

    def test_doc_change_makes_no_request(self):
        jobs.sync_job(SyncCtx(self.data))
        n = len(self.replay.calls)
        ctx = SyncCtx(self.data, reason="docs", docs={"shown": {"google:alex@example.com|alex@example.com": False}})
        jobs.sync_job(ctx)
        self.assertEqual(len(self.replay.calls), n)
        self.assertNotIn("Coffee with Sam", {r["title"] for r in ctx.artifacts[("agenda", "agenda.json")]})

    def test_tag_with_ai_now(self):
        ctx = SyncCtx(self.data, args={"ai": True})
        ctx.now = NOW.replace(hour=4)
        jobs.sync_job(ctx)
        self.assertEqual(len(self.replay.ai_calls()), 1)

    def test_optional_secrets_and_a_missing_google(self):
        ctx = SyncCtx(self.data, icloud=False, ical=False, ai=False)
        jobs.sync_job(ctx)
        self.assertEqual(self.replay.ai_calls(), [])
        self.assertFalse(any("icloud" in c["url"] for c in self.replay.calls))
        with self.assertRaises(NeedsConfig):
            jobs.sync_job(SyncCtx(self.data, google=False))

    def test_a_bad_icloud_secret_needs_config(self):
        ctx = SyncCtx(self.data)
        ctx.secrets["icloud"] = "just-an-email@icloud.com"
        with self.assertRaises(NeedsConfig):
            jobs.sync_job(ctx)

    def test_calendars_job(self):
        ctx = SyncCtx(self.data)
        jobs.calendars_job(ctx)
        self.assertEqual([a["kind"] for a in ctx.res["accounts"]], ["google", "icloud", "ical"])

    def test_account_name_prefers_the_email(self):
        self.assertEqual(platform.google_account({"account": "a1", "email": "x@y.z"}), "x@y.z")
        self.assertEqual(platform.google_account({"account": "x@y.z"}), "x@y.z")


@unittest.skipUnless(Path(os.environ.get("SLOWAPP_FONTS", "/opt/slowapp/fonts"), "AlfaSlabOne-Regular.ttf").is_file(),
                     "the base image's fonts")
class Render(unittest.TestCase):
    def ctx(self, fixture, now, surface="452x444.k4"):
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "fixtures" / fixture, tmp / "data")
        for d in ("in", "out"):
            (tmp / d).mkdir()
        t = datetime.fromisoformat(now)
        return Context(app="calendar", job="render", run_id="r_test", reason="fixture", mode="fixture", now=t, for_=t,
                       deadline=None, tz=replay.TZ, feeds=[Surface.from_name("agenda", surface)], data=tmp / "data",
                       out=tmp / "out", inp=tmp / "in", cache=tmp / "cache", secrets_dir=tmp / "secrets", manifest=MANIFEST)

    def deck(self, ctx):
        jobs.render(ctx)
        return json.loads((ctx.out / "feeds/agenda/deck.json").read_text())

    def test_examples(self):
        for ex in MANIFEST["example"]:
            ctx = self.ctx(Path(ex["fixture"]).name, ex["now"], ex["feeds"]["agenda"])
            deck = self.deck(ctx)
            self.assertEqual(deck["cards"][0]["id"], "home", ex["name"])
            self.assertGreater(deck["valid_until"], deck["valid_from"])
        self.assertEqual(deck["cards"][0]["peek"], "No calendar connected")     # the empty example

    def test_the_same_fixture_gives_the_same_pixels(self):
        a = (self.ctx("default", replay.NOW), self.ctx("default", replay.NOW))
        for c in a:
            self.deck(c)
        self.assertEqual(*[(c.out / "feeds/agenda/cards/home.png").read_bytes() for c in a])

    def test_cards_are_in_the_inks_and_overflow_to_later(self):
        agenda = json.loads((ROOT / "fixtures/default/artifacts/agenda/agenda.json").read_text())
        more = agenda + [dict(r, date="2026-11-02") for r in agenda if r["cal"] == "calendar" and r.get("tag") != "hidden"]
        for w in (452, 456):
            cards = draw.cards(Surface.from_name("agenda", f"{w}x444.k4"), more, NOW)
            self.assertEqual([c[0] for c in cards], ["home", "later"])
            for _, img, _, _ in cards:
                check_inks(img, "k4")
                self.assertEqual(img.size, (w, 444))

    def test_valid_until_is_the_next_end_today(self):
        agenda = json.loads((ROOT / "fixtures/default/artifacts/agenda/agenda.json").read_text())
        thu = datetime(2026, 10, 22, 17, 30, tzinfo=replay.TZ)
        self.assertEqual(draw.next_change(agenda, thu), datetime(2026, 10, 22, 21, 0, tzinfo=replay.TZ))
        late = datetime(2026, 10, 22, 23, 0, tzinfo=replay.TZ)
        self.assertEqual(draw.next_change(agenda, late), datetime(2026, 10, 23, tzinfo=replay.TZ))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(hasattr(Context, "token") and hasattr(Context, "write_artifact"), "contract 6 kit (slowapp-base 1.5.0)")
class KitContract6(unittest.TestCase):
    """The sync job on the real contract 6 kit: token files, value secrets, the artifact check."""

    def test_sync_through_the_kit(self):
        from slowapp_kit import execute
        tmp = Path(tempfile.mkdtemp())
        for d in ("data", "in/docs", "out", "secrets/google"):
            (tmp / d).mkdir(parents=True)
        (tmp / "secrets/google/alex@example.com.json").write_text(json.dumps(
            {"access_token": replay.GOOGLE_TOKEN, "expires_at": "2026-10-19T10:00:00-07:00", "scopes": []}))
        (tmp / "secrets/icloud").write_text(replay.ICLOUD_SECRET + "\n")
        (tmp / "secrets/ai_key").write_text(replay.AI_KEY)
        (tmp / "in/docs/names.json").write_text(json.dumps({"google:alex@example.com|pitchnight@google.com": "Pitch!"}))
        env = dict(SLOWAPP_CONTRACT="6", SLOWAPP_MODE="live", SLOWAPP_NOW=replay.NOW, SLOWAPP_REASON="schedule",
                   SLOWAPP_MANIFEST=str(ROOT / "slowapp.toml"), SLOWAPP_STATE=str(tmp / "data"), SLOWAPP_IN=str(tmp / "in"),
                   SLOWAPP_OUT=str(tmp / "out"), SLOWAPP_SECRETS=str(tmp / "secrets"), TZ="America/Vancouver")
        ctx = Context.from_env(job="sync", env=env)
        r = replay.Replay()
        with mock.patch.object(platform.http, "urllib_request", r):
            code, _ = execute("sync", ctx)
        self.assertEqual(code, 0)
        rows = json.loads((tmp / "out/artifacts/agenda/agenda.json").read_text())
        self.assertIn("Pitch!", {x["title"] for x in rows})
        self.assertFalse(any("calendar.google.com/calendar/ical" in c["url"] for c in r.calls))   # ical_url not set
        self.assertEqual(len(r.ai_calls()), 1)
