"""The calendar app's entrypoint: `python3 -m calapp job <sync | calendars | render>` (jobs.py)."""
import slowapp_kit as kit

from . import jobs  # noqa: F401  (registers the jobs)

kit.main()
