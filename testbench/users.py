"""Researcher accounts stored in _system.db.

Tables: users, auth_tokens, sessions_auth, memberships.
Participant data stays in per-project SQLite files and is unaffected.
"""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import current_app, g
from werkzeug.security import check_password_hash, generate_password_hash

# ---- common passwords (short list, checked on sign-up) ----
COMMON_PASSWORDS = frozenset([
    "1234567890", "password123", "qwerty12345", "letmein1234",
    "iloveyou12", "trustno1234", "welcome1234", "monkey12345",
    "master12345", "dragon12345", "login12345", "abc12345678",
    "admin12345", "password1234", "12345678901", "1234567891",
])

ROLES = ("owner", "editor", "viewer")
TOKEN_PURPOSES = ("verify", "reset", "invite", "login")

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    email_verified_at TEXT,
    is_platform_admin INTEGER NOT NULL DEFAULT 0,
    locale      TEXT NOT NULL DEFAULT 'en',
    created_at  TEXT NOT NULL,
    last_login_at TEXT,
    disabled_at TEXT
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT,
    meta_json   TEXT
);
CREATE TABLE IF NOT EXISTS sessions_auth (
    id_hash     TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    ip          TEXT,
    user_agent  TEXT
);
CREATE TABLE IF NOT EXISTS memberships (
    project_slug TEXT NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'viewer',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (project_slug, user_id)
);
CREATE TABLE IF NOT EXISTS invitations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_slug TEXT NOT NULL,
    email       TEXT NOT NULL COLLATE NOCASE,
    role        TEXT NOT NULL DEFAULT 'viewer',
    token_hash  TEXT NOT NULL,
    invited_by  INTEGER NOT NULL REFERENCES users(id),
    expires_at  TEXT NOT NULL,
    accepted_at TEXT
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode()).hexdigest()


def system_db():
    """Connection to _system.db, cached per request."""
    if "tb_users_db" not in g:
        path = Path(current_app.config["DATA_DIR"]) / "_system.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")   # readers never block the writer
        conn.execute("PRAGMA busy_timeout = 5000")  # wait instead of failing under concurrency
        from .moderation import PLATFORM_SCHEMA     # imported here: moderation reads this module
        conn.executescript(AUTH_SCHEMA + PLATFORM_SCHEMA)
        conn.commit()
        g.tb_users_db = conn
    return g.tb_users_db


def close_users_db(_exc=None):
    conn = g.pop("tb_users_db", None)
    if conn is not None:
        conn.close()


# ---------------------------------------------------------------- users

def create_user(email, name, password):
    """Returns (user_id, problems). problems is a list of error strings."""
    problems = []
    email = email.strip().lower()
    name = name.strip()
    if not email or "@" not in email:
        problems.append("invalid_email")
    if len(password) < 10:
        problems.append("password_too_short")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("password_common")
    if problems:
        return None, problems
    conn = system_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        problems.append("email_taken")
        return None, problems
    pw_hash = generate_password_hash(password)
    cur = conn.execute(
        "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (email, name, pw_hash, _now()))
    conn.commit()
    return cur.lastrowid, []


def get_user(user_id):
    return system_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_email(email):
    return system_db().execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()


def verify_password(user_row, password):
    if not user_row or not password:
        return False
    return check_password_hash(user_row["password_hash"], password)


def update_password(user_id, new_password):
    conn = system_db()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(new_password), user_id))
    conn.commit()


def set_email_verified(user_id):
    conn = system_db()
    conn.execute("UPDATE users SET email_verified_at = ? WHERE id = ?", (_now(), user_id))
    conn.commit()


def update_last_login(user_id):
    conn = system_db()
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
    conn.commit()


def update_user(user_id, **fields):
    allowed = {"name", "email", "locale", "disabled_at", "is_platform_admin"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = system_db()
    clause = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE users SET {clause} WHERE id = ?", (*sets.values(), user_id))
    conn.commit()


def delete_user(user_id):
    conn = system_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def list_users():
    return system_db().execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


# ---------------------------------------------------------------- tokens

def create_token(user_id, purpose, hours=24, meta=None):
    """Returns the raw token string (send this to the user). We store only the hash."""
    import json
    raw = secrets.token_urlsafe(32)
    conn = system_db()
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="milliseconds")
    conn.execute(
        "INSERT INTO auth_tokens (token_hash, user_id, purpose, expires_at, meta_json) VALUES (?, ?, ?, ?, ?)",
        (_hash_token(raw), user_id, purpose, expires, json.dumps(meta) if meta else None))
    conn.commit()
    return raw


def use_token(raw_token, purpose):
    """Returns the user row if the token is valid and unused, else None. Marks it as used."""
    conn = system_db()
    h = _hash_token(raw_token)
    row = conn.execute(
        "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = ?", (h, purpose)).fetchone()
    if not row:
        return None
    if row["used_at"]:
        return None
    if row["expires_at"] < _now():
        return None
    conn.execute("UPDATE auth_tokens SET used_at = ? WHERE token_hash = ?", (_now(), h))
    conn.commit()
    return get_user(row["user_id"])


# ---------------------------------------------------------------- sessions

def create_session(user_id, ip=None, user_agent=None):
    """Returns the raw session id (stored in the cookie). We store only its hash."""
    raw = secrets.token_urlsafe(32)
    conn = system_db()
    conn.execute(
        "INSERT INTO sessions_auth (id_hash, user_id, created_at, last_seen_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_hash_token(raw), user_id, _now(), _now(), ip, (user_agent or "")[:256]))
    conn.commit()
    return raw


def load_session_user(raw_session_id):
    """Returns the user row for a valid session, else None. Touches last_seen_at."""
    if not raw_session_id:
        return None
    conn = system_db()
    h = _hash_token(raw_session_id)
    row = conn.execute(
        "SELECT user_id FROM sessions_auth WHERE id_hash = ?", (h,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE sessions_auth SET last_seen_at = ? WHERE id_hash = ?", (_now(), h))
    conn.commit()
    user = get_user(row["user_id"])
    if user and user["disabled_at"]:
        return None
    return user


def revoke_session(raw_session_id):
    if not raw_session_id:
        return
    system_db().execute("DELETE FROM sessions_auth WHERE id_hash = ?", (_hash_token(raw_session_id),))
    system_db().commit()


def revoke_all_sessions(user_id):
    conn = system_db()
    conn.execute("DELETE FROM sessions_auth WHERE user_id = ?", (user_id,))
    conn.commit()


def list_sessions(user_id):
    return system_db().execute(
        "SELECT * FROM sessions_auth WHERE user_id = ? ORDER BY last_seen_at DESC",
        (user_id,)).fetchall()


# ---------------------------------------------------------------- memberships

def add_membership(slug, user_id, role="owner"):
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    conn = system_db()
    conn.execute(
        "INSERT INTO memberships (project_slug, user_id, role, created_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_slug, user_id) DO UPDATE SET role = excluded.role",
        (slug, user_id, role, _now()))
    conn.commit()


def remove_membership(slug, user_id):
    conn = system_db()
    conn.execute("DELETE FROM memberships WHERE project_slug = ? AND user_id = ?", (slug, user_id))
    conn.commit()


def get_membership(slug, user_id):
    return system_db().execute(
        "SELECT * FROM memberships WHERE project_slug = ? AND user_id = ?",
        (slug, user_id)).fetchone()


def project_members(slug):
    return system_db().execute(
        "SELECT m.*, u.email, u.name FROM memberships m JOIN users u ON m.user_id = u.id "
        "WHERE m.project_slug = ? ORDER BY m.created_at",
        (slug,)).fetchall()


def user_projects(user_id):
    return system_db().execute(
        "SELECT * FROM memberships WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)).fetchall()


# ---------------------------------------------------------------- authorization

def can(user, project_slug, action):
    """Check if a user can perform an action on a project.

    Actions: view, edit, reset, manage.
    Returns True/False. Platform admins can do everything.
    """
    if not user:
        return False
    if user["is_platform_admin"]:
        return True
    m = get_membership(project_slug, user["id"])
    if not m:
        return False
    role = m["role"]
    if action == "view":
        return role in ("owner", "editor", "viewer")
    if action == "edit":
        return role in ("owner", "editor")
    if action in ("reset", "manage"):
        return role == "owner"
    return False


# ---------------------------------------------------------------- invitations

def create_invitation(slug, email, role, invited_by, hours=168):
    """Invite someone to a project by email. Returns (invitation_id, raw_token). The raw token
    goes in the emailed link; only its hash is stored, so a leaked database cannot be used to
    accept invitations."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    raw = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="milliseconds")
    conn = system_db()
    conn.execute("DELETE FROM invitations WHERE project_slug = ? AND email = ? AND accepted_at IS NULL",
                 (slug, email.strip().lower()))
    cur = conn.execute(
        "INSERT INTO invitations (project_slug, email, role, token_hash, invited_by, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (slug, email.strip().lower(), role, _hash_token(raw), invited_by, expires))
    conn.commit()
    return cur.lastrowid, raw


def get_invitation(raw_token):
    """The pending, unexpired invitation for this token, or None."""
    row = system_db().execute(
        "SELECT * FROM invitations WHERE token_hash = ?", (_hash_token(raw_token),)).fetchone()
    if not row or row["accepted_at"] or row["expires_at"] < _now():
        return None
    return row


def accept_invitation(raw_token, user_id):
    """Grants the membership and marks the invitation used. Returns the slug, or None."""
    row = get_invitation(raw_token)
    if row is None:
        return None
    add_membership(row["project_slug"], user_id, row["role"])
    conn = system_db()
    conn.execute("UPDATE invitations SET accepted_at = ? WHERE id = ?", (_now(), row["id"]))
    conn.commit()
    return row["project_slug"]


def pending_invitations(slug):
    return system_db().execute(
        "SELECT * FROM invitations WHERE project_slug = ? AND accepted_at IS NULL "
        "AND expires_at >= ? ORDER BY id DESC", (slug, _now())).fetchall()


def revoke_invitation(slug, invitation_id):
    conn = system_db()
    conn.execute("DELETE FROM invitations WHERE id = ? AND project_slug = ? AND accepted_at IS NULL",
                 (invitation_id, slug))
    conn.commit()


def invitations_for_email(email):
    """Pending invitations waiting for someone who has just signed up."""
    return system_db().execute(
        "SELECT * FROM invitations WHERE email = ? AND accepted_at IS NULL AND expires_at >= ?",
        (email.strip().lower(), _now())).fetchall()
