"""Platform-level moderation, audit and counters, stored in _system.db.

Participant data stays in the per-project SQLite files. Nothing here reads it. The tables are:

- `reports`        abuse reports about a study, filed from /report by anyone;
- `project_flags`  a study taken offline, and whether it is approved for /explore;
- `platform_audit` who did what, for the platform admin log;
- `signup_events`  sign-up timestamps per IP, for the daily sign-up quota;
- `rate_events`    failed-login and other rate-limit events, so limits survive a restart
                   and are shared between gunicorn workers.

IP addresses in `platform_audit` and `signup_events` are personal data. `prune_ips()`
truncates them after a retention window; the operator runs it from `python -m testbench
retention`.
"""
import json
import time
from datetime import datetime, timedelta, timezone

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    project_slug TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    contact      TEXT NOT NULL DEFAULT '',
    ip           TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    handled_by   INTEGER,
    handled_at   TEXT,
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS reports_status ON reports (status, at);
CREATE TABLE IF NOT EXISTS project_flags (
    project_slug        TEXT PRIMARY KEY,
    offline_at          TEXT,
    offline_reason      TEXT NOT NULL DEFAULT '',
    explore_approved_at TEXT,
    set_by              INTEGER,
    updated_at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    user_id      INTEGER,
    actor        TEXT NOT NULL DEFAULT '',
    project_slug TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,
    detail_json  TEXT,
    ip           TEXT
);
CREATE INDEX IF NOT EXISTS platform_audit_at ON platform_audit (at);
CREATE TABLE IF NOT EXISTS signup_events (
    ip TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_events (
    key TEXT NOT NULL,
    at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_events_key ON rate_events (key, at);
"""

REPORT_STATUSES = ("open", "reviewed", "actioned", "dismissed")


def _db():
    from . import users
    return users.system_db()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- abuse reports

def add_report(project_slug, reason, contact="", ip=None):
    conn = _db()
    cur = conn.execute(
        "INSERT INTO reports (at, project_slug, reason, contact, ip) VALUES (?, ?, ?, ?, ?)",
        (_now(), (project_slug or "")[:64], (reason or "")[:4000], (contact or "")[:256], ip))
    conn.commit()
    return cur.lastrowid


def list_reports(status=None, limit=200):
    if status:
        return _db().execute("SELECT * FROM reports WHERE status = ? ORDER BY at DESC LIMIT ?",
                             (status, limit)).fetchall()
    return _db().execute("SELECT * FROM reports ORDER BY at DESC LIMIT ?", (limit,)).fetchall()


def count_open_reports():
    return _db().execute("SELECT COUNT(*) FROM reports WHERE status = 'open'").fetchone()[0]


def set_report_status(report_id, status, handled_by=None, note=""):
    if status not in REPORT_STATUSES:
        raise ValueError(f"status must be one of {REPORT_STATUSES}")
    conn = _db()
    conn.execute("UPDATE reports SET status = ?, handled_by = ?, handled_at = ?, note = ? WHERE id = ?",
                 (status, handled_by, _now(), (note or "")[:2000], report_id))
    conn.commit()


# ---------------------------------------------------------------- project flags

def _flags(slug):
    return _db().execute("SELECT * FROM project_flags WHERE project_slug = ?", (slug,)).fetchone()


def _set_flags(slug, **fields):
    conn = _db()
    conn.execute("INSERT INTO project_flags (project_slug, updated_at) VALUES (?, ?) "
                 "ON CONFLICT(project_slug) DO NOTHING", (slug, _now()))
    clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE project_flags SET {clause}, updated_at = ? WHERE project_slug = ?",
                 (*fields.values(), _now(), slug))
    conn.commit()


def take_offline(slug, reason, by_user_id=None):
    _set_flags(slug, offline_at=_now(), offline_reason=(reason or "")[:500], set_by=by_user_id)


def put_online(slug, by_user_id=None):
    _set_flags(slug, offline_at=None, offline_reason="", set_by=by_user_id)


def is_offline(slug):
    row = _flags(slug)
    return bool(row and row["offline_at"])


def offline_slugs():
    rows = _db().execute("SELECT project_slug FROM project_flags WHERE offline_at IS NOT NULL").fetchall()
    return {r["project_slug"] for r in rows}


def approve_explore(slug, approved=True, by_user_id=None):
    _set_flags(slug, explore_approved_at=_now() if approved else None, set_by=by_user_id)


def explore_approved_slugs():
    rows = _db().execute(
        "SELECT project_slug FROM project_flags WHERE explore_approved_at IS NOT NULL "
        "AND offline_at IS NULL").fetchall()
    return {r["project_slug"] for r in rows}


def flags_for(slugs):
    """{slug: row} for the slugs that have a flags row, for listing screens."""
    out = {}
    for row in _db().execute("SELECT * FROM project_flags").fetchall():
        if row["project_slug"] in slugs:
            out[row["project_slug"]] = row
    return out


# ---------------------------------------------------------------- audit log

def audit(action, user_id=None, actor="", project_slug="", detail=None, ip=None):
    conn = _db()
    conn.execute(
        "INSERT INTO platform_audit (at, user_id, actor, project_slug, action, detail_json, ip) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), user_id, actor[:256], project_slug[:64], action[:64],
         json.dumps(detail) if detail is not None else None, ip))
    conn.commit()


def list_audit(limit=300, project_slug=None):
    if project_slug:
        return _db().execute(
            "SELECT * FROM platform_audit WHERE project_slug = ? ORDER BY at DESC LIMIT ?",
            (project_slug, limit)).fetchall()
    return _db().execute("SELECT * FROM platform_audit ORDER BY at DESC LIMIT ?", (limit,)).fetchall()


def anonymise_user(user_id):
    """Keep the audit trail but drop the link to a deleted account."""
    conn = _db()
    conn.execute("UPDATE platform_audit SET user_id = NULL, actor = 'deleted account', ip = NULL "
                 "WHERE user_id = ?", (user_id,))
    conn.commit()


# ---------------------------------------------------------------- sign-up counter

def record_signup(ip):
    conn = _db()
    conn.execute("INSERT INTO signup_events (ip, at) VALUES (?, ?)", (ip, _now()))
    conn.commit()


def signups_since(ip, hours=24):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="milliseconds")
    return _db().execute("SELECT COUNT(*) FROM signup_events WHERE ip = ? AND at >= ?",
                         (ip, cutoff)).fetchone()[0]


# ---------------------------------------------------------------- retention

def prune_ips(days=30):
    """Drop IP addresses older than `days` from the audit log, and old sign-up rows entirely."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="milliseconds")
    conn = _db()
    n1 = conn.execute("UPDATE platform_audit SET ip = NULL WHERE ip IS NOT NULL AND at < ?",
                      (cutoff,)).rowcount
    n2 = conn.execute("DELETE FROM signup_events WHERE at < ?", (cutoff,)).rowcount
    n3 = conn.execute("UPDATE sessions_auth SET ip = NULL WHERE ip IS NOT NULL AND last_seen_at < ?",
                      (cutoff,)).rowcount
    conn.commit()
    return {"audit_ips_cleared": n1, "signup_events_deleted": n2, "session_ips_cleared": n3}


def prune_rate_events(older_than_seconds=86400):
    conn = _db()
    n = conn.execute("DELETE FROM rate_events WHERE at < ?",
                     (time.time() - older_than_seconds,)).rowcount
    conn.commit()
    return n


def prune_tokens():
    """Expired or used one-time tokens serve no purpose."""
    conn = _db()
    n = conn.execute("DELETE FROM auth_tokens WHERE used_at IS NOT NULL OR expires_at < ?",
                     (_now(),)).rowcount
    conn.commit()
    return n
