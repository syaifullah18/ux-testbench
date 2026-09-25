"""Rate limiting for failed logins and sign-ups.

Events are stored in `rate_events` in _system.db, so a limit is shared between gunicorn
workers and survives a restart. An in-memory dictionary is the fallback for the cases where no
database is reachable: outside an application context, and in the test suite. That fallback is
per process, which is the behaviour this module had before.

`python -m testbench retention` clears old rows.
"""
import logging
import time
from collections import defaultdict

log = logging.getLogger(__name__)

_MEMORY = defaultdict(list)


def _db():
    """The system connection, or None when we are not inside a request."""
    try:
        from flask import current_app, has_app_context
        if not has_app_context():
            return None
        if current_app.config.get("RATE_LIMIT_BACKEND") == "memory":
            return None
        from . import users
        return users.system_db()
    except Exception:
        log.debug("rate limit falling back to memory", exc_info=True)
        return None


def record_failed_login(ip, context):
    """Record one failed attempt for an IP in a context ('auth_login', 'signup', …)."""
    key = f"{ip}:{context}"
    conn = _db()
    if conn is None:
        _MEMORY[key].append(time.time())
        return
    try:
        conn.execute("INSERT INTO rate_events (key, at) VALUES (?, ?)", (key, time.time()))
        conn.commit()
    except Exception:
        log.debug("could not write rate event", exc_info=True)
        _MEMORY[key].append(time.time())


def attempts(ip, context, window_seconds=900):
    key = f"{ip}:{context}"
    cutoff = time.time() - window_seconds
    conn = _db()
    if conn is None:
        _MEMORY[key] = [t for t in _MEMORY[key] if t >= cutoff]
        return len(_MEMORY[key])
    try:
        return conn.execute("SELECT COUNT(*) FROM rate_events WHERE key = ? AND at >= ?",
                            (key, cutoff)).fetchone()[0]
    except Exception:
        log.debug("could not read rate events", exc_info=True)
        return len(_MEMORY[key])


def is_rate_limited(ip, context, max_attempts=5, window_seconds=900):
    return attempts(ip, context, window_seconds) >= max_attempts


def clear(ip, context):
    """Called after a successful login, so one bad guess does not count against a good user."""
    key = f"{ip}:{context}"
    _MEMORY.pop(key, None)
    conn = _db()
    if conn is None:
        return
    try:
        conn.execute("DELETE FROM rate_events WHERE key = ?", (key,))
        conn.commit()
    except Exception:
        log.debug("could not clear rate events", exc_info=True)
