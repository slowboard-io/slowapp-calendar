# slowapp-calendar

A [slowapp](https://github.com/mike-anderson/slowapp-base) for a Slowboard e-paper dashboard: a
household's Google and iCloud calendars (and any secret iCal address), sorted by the household's
own AI key into hidden, regular and important, as an agenda for one third of the board.

It is epaper-dashboard's `tools/calsync.py` ported to **contract 6** (slowapp-base 1.5.0, the
secrets track's S6): a `sync` job fetches with the secrets and writes the `agenda` artifact, and
`render` draws it with no network and no secrets. Standard library and Pillow only, so it builds
on the slim `slowapp-python` base.

| Job | Kind | What it does |
|---|---|---|
| `sync` | sync, every 15 min (`min_every` 15 min) | Fetches every connected account, tags new series, writes `/out/artifacts/agenda/agenda.json`. Secrets `google` (OAuth, several accounts), `icloud`, `ical_url`, `ai_key`, all optional; with none of the first three it exits 5 (needs config). `args.ai = true` is "Tag with AI now". A doc's `on_change` runs it too; then it rebuilds from the last fetch without the network or the AI. |
| `calendars` | sync, on demand | Every account's calendars for the UI's picker, as `result.json`. |
| `render` | render | The `agenda` slot feed from `/in/artifacts/agenda/`: a home card, and a "later" card when the rows run over; and the `full` 540x960 g16 screen for a Slowboard 470. |

`agenda.json` keeps calsync's shape (`date`, `title`, `start`, `end`, `cal` = `holiday` or
`calendar`, `reps`, `tag`), which the legacy renderer and the weather app's `calendar:agenda`
import read.

The docs are calsync's files: `tags` (series → `{tag, by, why, at}`; the household's tags win, and
calsync's AI tags are taken as cached), `names` (series → display name) and `shown`
(calendar key → `false` to leave it off). The sync job writes one doc back, `upcoming` (every
upcoming series with its title, calendar, next date, count and tag, and who set it: `user`,
`fixed` for bin days, `ai` or `rules`), for the page.

The page (`ui/page.html`, the bridge vendored from slowapp-base and inlined at build by
`tools/build_ui.py`) lists **What's on the wall**: each series with Hidden / Regular / Important
and Auto, written to `tags` as the household's choice (its `on_change` re-syncs without fetching).
It connects Google accounts (bridge protocol 2, `secrets.connect`), sets the iCloud, iCal address
and AI key secrets, and has Sync now and Tag with AI now. Still to come: the calendar picker
(`shown`) and renaming a series (`names`).

## The AI

A decision model with the household's key (`ai_provider`: `jev`, TypeSafe's Jev
(`typesafe-jev-1.13.0`) on DigitalOcean's System One API, the default; or `gemini`, Google's
decisions API). The month goes in as shared context and each series is one choice question over the
three tags, answered with calibrated probabilities, so a reply can only be a tag; the probability is
kept as the tag's "why". `ai_base_url` and `ai_model` override the provider's. A series is asked
about once: its tag is cached in `/data/ai_cache.json` under a hash of what the AI was shown
(series, title, calendar, start time, repeating), so a sync with nothing new makes no AI call. New
series are sent at the first sync from `ai_hour` (05:00) each day, or on "Tag with AI now"; until
then they get the rules' tags (the legacy renderer's score). Only titles, times, calendar names
and repeat counts are sent.

## Layout

| Path | What it is |
|---|---|
| `calapp/occ.py` | The occurrence dict (calsync's, so series and calendar keys carry over) and the sync window, 28 days back and 14 ahead |
| `calapp/google.py` | Calendar API v3 with an access token: `calendars()`, `events()`, `fetch()` |
| `calapp/ics.py` | iCalendar parsing and recurrence expansion (RRULE, RDATE, EXDATE, RECURRENCE-ID), stdlib only |
| `calapp/caldav.py` | A small CalDAV client for iCloud: principal, home, calendars, a time-range REPORT |
| `calapp/sources.py` | One `Source` per account: Google, iCloud, each iCal address |
| `calapp/merge.py` | Accounts together, duplicates dropped (Google before iCloud before iCal addresses) |
| `calapp/rules.py` | The legacy renderer's rules score and near-duplicate merge |
| `calapp/ai.py` | The prompt, the tagger (injectable HTTP client), reply checking, the cache key |
| `calapp/agenda.py` | Tags, names and shown calendars applied: the agenda rows |
| `calapp/sync.py` | One sync and its `/data` state; the calendar picker's list |
| `calapp/draw.py` | The agenda card in the four inks |
| `calapp/full.py` | The Slowboard 470's whole screen (`full`, 540x960 in 16 greys) |
| `calapp/platform.py` | Everything contract 6: tokens, secrets, the artifact, docs, the deadline-aware client |
| `calapp/jobs.py`, `__main__.py` | The three jobs and the entrypoint |
| `ui/page.html`, `ui/bridge.js`, `tools/build_ui.py` | The page, the vendored bridge, and the build step that inlines one into the other |
| `fixtures/sources/` | Saved Google responses, CalDAV responses, an iCloud `.ics` and an iCal address's `.ics`, and the docs |
| `fixtures/default/artifacts/agenda/` | The sync of those at the default example's time: what the examples render |
| `tests/` | Unit tests, run in the base image; `tests/replay.py` answers requests from `fixtures/sources/` |

## Building

    TAG=0.2.0 docker buildx bake --push

`docker-bake.hcl` builds for amd64 and arm64 and puts `org.opencontainers.image.source` on the
multi-arch index too, which is where GitHub looks to connect the package to this repo. The
manifest has no version key: the image tag is the release.

## Running the tests

    docker run --rm -v "$PWD":/src -w /src --entrypoint python3 \
      ghcr.io/mike-anderson/slowapp-python:1.5.0 -m unittest discover -s tests

After changing a source fixture or the tagging logic, rewrite the render fixture with
`UPDATE_FIXTURES=1` in the same command (`-e UPDATE_FIXTURES=1`).
