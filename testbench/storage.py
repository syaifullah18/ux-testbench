"""One SQLite file per project, so projects never share rows and one can be archived or
deleted without touching the others. Module types store answers as JSON keyed by the ids in
their YAML, which keeps the schema the same for every study."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, g

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    module_id TEXT NOT NULL,
    step TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (participant_id, module_id)
);
CREATE TABLE IF NOT EXISTS answers (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    page TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, page)
);
CREATE TABLE IF NOT EXISTS task_results (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    variant TEXT NOT NULL,
    task_id TEXT NOT NULL,
    time_ms INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    scroll_reversals INTEGER NOT NULL DEFAULT 0,
    first_click TEXT,
    click_path TEXT,
    viewport_w INTEGER,
    answer TEXT,
    gave_up INTEGER NOT NULL DEFAULT 0,
    ease INTEGER,
    auto_pass INTEGER,
    grade TEXT,
    observer_note TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, position, task_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    admin_ip TEXT,
    details TEXT
);
"""

def audit(conn, action, ip=None, details=None):
    import json
    if details is not None and not isinstance(details, str):
        details = json.dumps(details)
    conn.execute("INSERT INTO audit_log (timestamp, action, admin_ip, details) VALUES (?, ?, ?, ?)",
                 (now_iso(), action, ip, details))
    conn.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def db_path(slug):
    return Path(current_app.config["DATA_DIR"]) / f"{slug}.db"


def connect(slug):
    """Connection for one project, cached for the request."""
    conns = g.setdefault("tb_conns", {})
    if slug not in conns:
        path = db_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
        conns[slug] = conn
    return conns[slug]


def close_all(_exc=None):
    for conn in g.pop("tb_conns", {}).values():
        conn.close()


def reset(slug):
    conn = connect(slug)
    for table in ("task_results", "answers", "sessions", "participants"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


# ---------------------------------------------------------------- common queries

def session_for(conn, participant_id, module_id):
    return conn.execute("SELECT * FROM sessions WHERE participant_id = ? AND module_id = ?",
                        (participant_id, module_id)).fetchone()


def module_answers(conn, session_id):
    """All pages of one session merged into one dict (later pages win on clashes)."""
    merged = {}
    for row in conn.execute("SELECT data FROM answers WHERE session_id = ? ORDER BY updated_at", (session_id,)):
        merged.update(loads(row["data"], {}))
    return merged


def bulk_module_answers(conn, session_ids):
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(f"SELECT session_id, data FROM answers WHERE session_id IN ({placeholders}) ORDER BY session_id, updated_at", tuple(session_ids)).fetchall()
    out = {sid: {} for sid in session_ids}
    for row in rows:
        out[row["session_id"]].update(loads(row["data"], {}))
    return out


def bulk_answers_by_page(conn, session_ids):
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(f"SELECT session_id, page, data FROM answers WHERE session_id IN ({placeholders})", tuple(session_ids)).fetchall()
    out = {sid: {} for sid in session_ids}
    for row in rows:
        out[row["session_id"]][row["page"]] = loads(row["data"], {})
    return out


def bulk_task_results(conn, session_ids):
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = conn.execute(f"SELECT * FROM task_results WHERE session_id IN ({placeholders})", tuple(session_ids)).fetchall()
    out = {sid: {} for sid in session_ids}
    for r in rows:
        sid, pos, tid = r["session_id"], r["position"], r["task_id"]
        out[sid].setdefault(pos, {})[tid] = r
    return out


def page_answers(conn, session_id, page):
    row = conn.execute("SELECT data FROM answers WHERE session_id = ? AND page = ?", (session_id, page)).fetchone()
    return loads(row["data"], {}) if row else {}


def save_page(conn, session_id, page, data):
    conn.execute("INSERT INTO answers (session_id, page, data, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(session_id, page) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                 (session_id, page, dumps(data), now_iso()))
