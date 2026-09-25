"""Data retention helpers.

Participant data should not be kept forever just because storage is cheap. A study that closed a
year ago has served its purpose, and keeping its answers is a liability for the researcher and
for the participants who trusted them.

Deleting is deliberately not automatic. This module reports what has gone stale so the operator
can tell the owners first; `python -m testbench retention` prints that list.
"""
from datetime import datetime, timedelta, timezone


def _last_activity(app, slug):
    from . import storage
    try:
        conn = storage.connect(slug)
        return conn.execute("SELECT MAX(last_seen_at) FROM participants").fetchone()[0]
    except Exception:
        return None


def stale_closed_projects(app, months=12):
    """[(slug, last activity)] for closed studies with no participant activity in `months`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30 * months)).isoformat(timespec="milliseconds")
    registry = app.extensions["testbench"]
    out = []
    for project in registry.projects.values():
        if project.status != "closed":
            continue
        last = _last_activity(app, project.slug)
        if last is None or last < cutoff:
            out.append((project.slug, last or "never"))
    return sorted(out)


def owners_to_notify(slug):
    """Email addresses to warn before a study's data is deleted."""
    from . import users
    return [m["email"] for m in users.project_members(slug) if m["role"] == "owner"]
