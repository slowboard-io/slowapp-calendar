"""The calendar slowapp: Google and iCloud calendars (and secret iCal addresses), tagged hidden /
regular / important by the household's AI, as an agenda artifact and an agenda slot feed.

A port of epaper-dashboard tools/calsync.py to contract 6. The logic is plain modules that take
their HTTP client as an argument, so tests run them on saved responses:

  occ        the occurrence dict every source produces (calsync's), and the sync window
  google     Google Calendar API v3, given an access token
  ics        iCalendar text -> occurrences, with recurrences expanded (stdlib only)
  caldav     a small CalDAV client (iCloud), given an Apple ID and app-specific password
  sources    one Source per connected account: Google, iCloud, iCal address
  merge      the accounts' occurrences together, duplicates dropped
  rules      the renderer's rules score (epaper-dashboard renderer/events.py), the fallback tags
  ai         AI tagging through an OpenAI-compatible endpoint, with a cache keyed by series hash
  agenda     tags, display names and shown calendars applied: agenda.json rows (calsync's shape)
  sync       one sync: fetch, tag, build; state kept in /data
  draw       the agenda card in the panel's four inks
  platform   everything that touches contract 6 (tokens, secrets, artifacts, docs): one file
"""
