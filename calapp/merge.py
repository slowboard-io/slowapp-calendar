"""The accounts' occurrences together, without duplicates.

  - The same occurrence twice in one account (one Google event on two of its calendars) is kept
    once: the first calendar's.
  - The same event from two accounts (a Google calendar connected and also given as its secret iCal
    address, or an invitation on both Google and iCloud) is kept from the account that comes first
    in SOURCE_ORDER: Google, then iCloud, then iCal addresses. "The same" is the same day, start,
    end and title, ignoring case and spacing.

The result keeps calsync's events.json shape, {account id: [occurrence]}, so a failed account
keeps its last good copy.
"""
import re

SOURCE_ORDER = ("google", "icloud", "ical")


def _rank(account):
    kind = account.split(":", 1)[0]
    return SOURCE_ORDER.index(kind) if kind in SOURCE_ORDER else len(SOURCE_ORDER)


def same_key(o):
    return (o["date"], o["start"], o["end"], re.sub(r"\s+", " ", o["title"]).strip().casefold())


def merge(events):
    """{account: [occurrence]} -> {account: [occurrence]} with duplicates dropped."""
    out, ids, keys = {}, set(), {}
    for account in sorted(events, key=lambda a: (_rank(a), a)):
        kept = []
        for o in events[account]:
            if o["id"] in ids:
                continue
            k = same_key(o)
            if keys.get(k, account) != account:
                continue
            ids.add(o["id"])
            keys.setdefault(k, account)
            kept.append(o)
        out[account] = kept
    return out


def flatten(events):
    return [o for acct in sorted(events, key=lambda a: (_rank(a), a)) for o in events[acct]]
