"""The jobs (the entrypoint, __main__.py, runs them: `python3 -m calapp job <name>`).

  sync       kind sync, every 15 min: fetch every connected account, tag new series (AI once a
             day, from the cache otherwise), write the `agenda` artifact. A doc's on_change runs it
             too; then it rebuilds from the last fetch without the network or the AI.
  calendars  kind sync, run by the UI: every account's calendars for the picker (result.json).
  render     no network, no secrets: the `agenda` slot feed from the artifact.
"""
from datetime import timedelta

import slowapp_kit as kit
from slowapp_kit import Card, ContractError

from . import draw, platform, sync


@kit.job("sync")
def sync_job(ctx):
    if ctx.fixture:
        raise ContractError("sync fetches calendars: fixtures render from a saved artifacts/agenda/ instead")
    state = sync.State(ctx.data)
    refetch = not platform.doc_change(ctx)
    req = platform.request(ctx)
    srcs = platform.account_sources(ctx, req) if refetch else []
    res = sync.run(ctx.now, srcs, platform.docs(ctx), state, tagger=platform.tagger(ctx, req) if refetch else None,
                   ai_hour=int(ctx.settings.get("ai_hour", 5)), force_ai=platform.force_ai(ctx), refetch=refetch)
    platform.write_agenda(ctx, res.rows)
    state.save()
    kit.log("calendar: " + "; ".join(res.lines))
    ctx.write_summary("ok" if res.ok else "warn", res.lines,
                      [{"label": "Sync now", "job": "sync"}])


@kit.job("calendars")
def calendars_job(ctx):
    req = platform.request(ctx)
    ctx.result({"accounts": sync.calendars(platform.account_sources(ctx, req), platform.docs(ctx).shown)})


@kit.job("render")
def render(ctx):
    surface = ctx.feed("agenda")
    if surface is None:
        return
    agenda = platform.read_agenda(ctx)
    t = ctx.for_
    ctx.write_feed("agenda", [Card(cid, img, title, peek=peek) for cid, img, title, peek in draw.cards(surface, agenda, t)],
                   valid_from=t, valid_until=max(draw.next_change(agenda, t), t + timedelta(minutes=5)))

