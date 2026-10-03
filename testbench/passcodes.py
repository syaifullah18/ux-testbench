"""Project passcodes. An environment variable wins when set (useful for deployments managed as
code); otherwise the passcode set in the Studio is used, stored only as a salted hash in the
platform database. A project with neither stays closed."""
import hmac
import os

from werkzeug.security import check_password_hash, generate_password_hash

from .storage import now_iso

KINDS = ("participant", "admin")


PASSCODES_SCHEMA = """
CREATE TABLE IF NOT EXISTS passcodes (slug TEXT NOT NULL, kind TEXT NOT NULL,
    hash TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (slug, kind));
"""


def _db():
    from . import users
    return users.system_db()


def close(_exc=None):
    pass   # the platform connection is closed with all the others, in db.close_all()


def env_name(project, kind):
    return project.passcode_env if kind == "participant" else project.admin_passcode_env


def source(project, kind):
    """'env', 'studio' or None."""
    if os.environ.get(env_name(project, kind)):
        return "env"
    row = _db().execute("SELECT 1 FROM passcodes WHERE slug = ? AND kind = ?", (project.slug, kind)).fetchone()
    return "studio" if row else None


def check(project, kind, given):
    if not given:
        return False
    env = os.environ.get(env_name(project, kind))
    if env:
        return hmac.compare_digest(env.encode(), given.encode())
    row = _db().execute("SELECT hash FROM passcodes WHERE slug = ? AND kind = ?", (project.slug, kind)).fetchone()
    return bool(row) and check_password_hash(row["hash"], given)


def set_passcode(slug, kind, plain):
    conn = _db()
    conn.execute("INSERT INTO passcodes (slug, kind, hash, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(slug, kind) DO UPDATE SET hash = excluded.hash, updated_at = excluded.updated_at",
                 (slug, kind, generate_password_hash(plain), now_iso()))
    conn.commit()


def clear(slug, kind=None):
    conn = _db()
    if kind:
        conn.execute("DELETE FROM passcodes WHERE slug = ? AND kind = ?", (slug, kind))
    else:
        conn.execute("DELETE FROM passcodes WHERE slug = ?", (slug,))
    conn.commit()


def superadmin_ok(given):
    expected = os.environ.get("SUPERADMIN_PASSCODE", "")
    return bool(expected and given) and hmac.compare_digest(expected.encode(), given.encode())
