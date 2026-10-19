"""Everything that touches contract 6 (slowapp-base 1.5.0), in one file: the run's OAuth tokens and
value secrets, the agenda artifact, the docs, the deadline-aware HTTP client. The rest of the app
takes plain values, so when contract 6 settles (or changes) this is the file to change.

Contract 6 as the app uses it (slowapp-base MANIFEST.md, branch contract6/secrets-sync):

  ctx.token("google")                [secrets.google] kind = "oauth", multiple = true: a list of
                                     {access_token, expires_at, scopes, account}, one per connected
                                     Google account (/run/secrets/google/<account>.json)
  ctx.secret("icloud", optional)     value secrets: /run/secrets/<name>, None when not set
  ctx.write_artifact("agenda", ...)  sync: /out/artifacts/agenda/agenda.json
  ctx.read_artifact("agenda", ...)   render: /in/artifacts/agenda/agenda.json (in fixture mode the
                                     fixture's artifacts/agenda/, which is /data/artifacts/agenda/)
  ctx.doc(name), ctx.args            contract 4, unchanged
  ctx.reason == "docs"               the run is a doc's on_change: rebuild, don't fetch

Open points, settled in the hub's S4 (OAuth) and not here:
  - The account name. Series and calendar keys start "google:<account>", and calsync's were
    "google:<email>", so tags and names carry over (S7) only if the hub names each account by its
    Google address. google_account() prefers an "email" field in the token file, then "account".
"""
from pathlib import Path

from slowapp_kit import ContractError, NeedsConfig, Transient

from . import ai, http, sources
from .sync import Docs

AGENDA, AGENDA_FILE = "agenda", "agenda.json"
DEADLINE_MARGIN_S = 5


def require_contract6(ctx):
    if not all(hasattr(ctx, a) for a in ("token", "write_artifact", "read_artifact")):
        raise ContractError("this job needs slowapp-base 1.5.0 or later (contract 6: token, artifacts)")


def request(ctx):
    """http.urllib_request with its timeout capped to the job's deadline. No network in fixture mode."""
    def req(method, url, body=None, headers=None, timeout=30):
        if ctx.fixture:
            raise ContractError(f"network use in fixture mode: {method} {url.split('?')[0]}")
        left = ctx.remaining()
        if left is not None:
            timeout = min(timeout, left - DEADLINE_MARGIN_S)
            if timeout <= 0:
                raise Transient("no time left before the deadline")
        return http.urllib_request(method, url, body, headers, timeout)
    return req


def google_account(tok):
    return str(tok.get("email") or tok.get("account") or "google")


def account_sources(ctx, req):
    """The run's connected accounts as Sources: every Google account, the iCloud account, and each
    secret iCal address."""
    require_contract6(ctx)
    out = []
    toks = ctx.token("google")
    for tok in (toks if isinstance(toks, list) else [toks] if toks else []):
        out.append(sources.google_source(req, tok["access_token"], google_account(tok)))
    icloud = ctx.secret("icloud", optional=True)
    if icloud:
        try:
            out.append(sources.icloud_source(req, icloud))
        except ValueError as e:
            raise NeedsConfig(str(e))
    out += sources.ical_sources(req, ctx.secret("ical_url", optional=True))
    return out


def tagger(ctx, req):
    """The AI tagger with the household's key and endpoint, or None without a key."""
    key = ctx.secret("ai_key", optional=True)
    if not key:
        return None
    s = ctx.settings
    return ai.tagger(req, key.strip(), s.get("ai_base_url") or ai.DEFAULT_BASE_URL, s.get("ai_model") or ai.DEFAULT_MODEL)


def docs(ctx):
    def get(name):
        v = ctx.doc(name, {})
        return v if isinstance(v, dict) else {}
    return Docs(tags=get("tags"), names=get("names"), shown=get("shown"))


def doc_change(ctx):
    """True when the run is a doc's on_change: the agenda is rebuilt without fetching."""
    return ctx.reason == "docs"


def force_ai(ctx):
    return bool(ctx.args.get("ai"))


def write_agenda(ctx, rows):
    require_contract6(ctx)
    ctx.write_artifact(AGENDA, AGENDA_FILE, rows)


def read_agenda(ctx):
    """The last agenda the sync job wrote, or None before the first sync."""
    if hasattr(ctx, "read_artifact"):
        return ctx.read_artifact(AGENDA, AGENDA_FILE, None)
    import json                                         # a pre-contract-6 kit: the same paths by hand
    for d in [Path(ctx.inp) / "artifacts" / AGENDA] + ([Path(ctx.data) / "artifacts" / AGENDA] if ctx.fixture else []):
        f = d / AGENDA_FILE
        if f.is_file():
            return json.loads(f.read_text())
    return None
