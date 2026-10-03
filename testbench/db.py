"""Database connections, for SQLite (the default) and PostgreSQL (when DATABASE_URL is set).

Everything that touches a database goes through `study(slug)` or `system()`; no other module
imports a driver. The rest of the code writes SQLite-flavoured SQL with `?` placeholders and
reads rows by name or by position, and this module makes that work on both backends:

- SQLite: one file per study in DATA_DIR plus `_system.db`, exactly as before.
- PostgreSQL: one schema per study (`study_<slug>`) plus one for the platform (`testbench`).
  A study's connection runs with its own `search_path`, so a query can no more reach another
  study's rows than it could open another study's file. That is the isolation ADR 003 chose
  one file per study for.

On PostgreSQL connections run in autocommit mode. The code already commits after each logical
write, and several callers deliberately swallow a failed statement (rate limiting, for one);
in a PostgreSQL transaction that failure would poison every later statement on the connection.
`commit()` and `rollback()` are kept as no-ops so call sites do not change.
"""
import re
import sqlite3
from pathlib import Path

from flask import current_app, g, has_app_context

SYSTEM = "_system"   # the key under which the platform database is cached


# ---------------------------------------------------------------- which backend

def database_url(app=None):
    app = app or (current_app if has_app_context() else None)
    return (app.config.get("DATABASE_URL") if app is not None else None) or None


def is_postgres(app=None):
    url = database_url(app)
    return bool(url) and url.split(":", 1)[0] in ("postgres", "postgresql")


def _prefix():
    """Prepended to every PostgreSQL schema name. Empty in production; the test suite sets a
    unique one per app so tests share one database without seeing each other's rows."""
    return current_app.config.get("DB_SCHEMA_PREFIX", "") or ""


def schema_name(slug):
    """The PostgreSQL schema for a study, or for the platform when slug is SYSTEM."""
    base = "testbench" if slug == SYSTEM else f"study_{slug}"
    return f"{_prefix()}{base}"


def _quote(ident):
    return '"' + ident.replace('"', '""') + '"'


# ---------------------------------------------------------------- errors

def _integrity_errors():
    errors = (sqlite3.IntegrityError,)
    try:
        import psycopg
        errors += (psycopg.errors.IntegrityError,)
    except ImportError:
        pass
    return errors


IntegrityError = _integrity_errors()


# ---------------------------------------------------------------- SQLite

class SqliteConnection(sqlite3.Connection):
    dialect = "sqlite"


def _open_sqlite(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, factory=SqliteConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")    # readers never block the writer
    conn.execute("PRAGMA busy_timeout = 5000")   # wait instead of failing under concurrency
    return conn


def sqlite_path(slug):
    return Path(current_app.config["DATA_DIR"]) / f"{slug}.db"


# ---------------------------------------------------------------- PostgreSQL

_POOLS = {}


def _pool():
    url = database_url()
    pool = _POOLS.get(url)
    if pool is None:
        from psycopg_pool import ConnectionPool
        pool = ConnectionPool(url, min_size=1, max_size=int(current_app.config.get("DB_POOL_MAX", 5)),
                              kwargs={"autocommit": True}, open=True, name="testbench")
        _POOLS[url] = pool
    return pool


class Row(tuple):
    """A row readable by column name and by position, like sqlite3.Row."""
    __slots__ = ()
    _keys = ()

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._index[key])
            except KeyError:
                raise IndexError(f"No item with that key: {key}") from None
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._keys)


def _row_factory(cursor):
    desc = cursor.description
    if desc is None:
        return tuple
    keys = tuple(d.name for d in desc)
    cls = type("Row", (Row,), {"_keys": keys, "_index": {k: i for i, k in enumerate(keys)}})
    return lambda values: cls(values)


_QMARK = re.compile(r"'(?:[^']|'')*'|\?|%")


def translate(sql, has_params):
    """SQLite placeholders to psycopg ones. `?` outside a string literal becomes `%s`; a literal
    `%` is doubled so psycopg does not read it as a placeholder. Without parameters psycopg does
    no substitution at all, so the SQL is sent as written."""
    if not has_params:
        return sql

    def repl(m):
        tok = m.group(0)
        if tok == "?":
            return "%s"
        if tok == "%":
            return "%%"
        return tok.replace("%", "%%")   # a string literal: keep it, escape any % inside
    return _QMARK.sub(repl, sql)


def _adapt(params):
    """bool to int, as SQLite stores it; PostgreSQL would refuse a boolean in an INTEGER column."""
    if not params:
        return None
    if isinstance(params, dict):
        return {k: int(v) if isinstance(v, bool) else v for k, v in params.items()}
    return [int(v) if isinstance(v, bool) else v for v in params]


_DDL_RULES = [
    (re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.I), "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"),
    (re.compile(r"\s+COLLATE\s+NOCASE", re.I), ""),
    (re.compile(r"\bREAL\b"), "DOUBLE PRECISION"),
]


def postgres_ddl(script):
    """A schema written for SQLite, made valid for PostgreSQL."""
    for pattern, repl in _DDL_RULES:
        script = pattern.sub(repl, script)
    return script


class PostgresConnection:
    """The slice of the sqlite3.Connection interface this code base uses."""
    dialect = "postgres"

    def __init__(self, raw, pool, schema):
        self.raw, self._pool, self.schema = raw, pool, schema

    def execute(self, sql, params=()):
        return self.raw.execute(translate(sql, bool(params)), _adapt(params))

    def executemany(self, sql, seq):
        rows = [_adapt(p) for p in seq]
        cur = self.raw.cursor()
        if rows:
            cur.executemany(translate(sql, True), rows)
        return cur

    def executescript(self, script):
        self.raw.execute(postgres_ddl(script))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        if self.raw is not None:
            self.raw.execute("RESET search_path")
            self._pool.putconn(self.raw)
            self.raw = None


def _open_postgres(slug):
    pool = _pool()
    raw = pool.getconn()
    raw.row_factory = _row_factory
    schema = schema_name(slug)
    raw.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}")
    raw.execute(f"SET search_path TO {_quote(schema)}")
    return PostgresConnection(raw, pool, schema)


# ---------------------------------------------------------------- connections

def _cache():
    return g.setdefault("tb_db", {})


def connect(slug, schema):
    """A connection to one study (or the platform, slug=SYSTEM), with its schema applied,
    cached for the rest of the request."""
    cache = _cache()
    if slug not in cache:
        if is_postgres():
            conn = _open_postgres(slug)
        else:
            conn = _open_sqlite(sqlite_path(slug))
        conn.executescript(schema)
        conn.commit()
        cache[slug] = conn
    return cache[slug]


def close_all(_exc=None):
    for conn in g.pop("tb_db", {}).values():
        try:
            conn.close()
        except Exception:
            pass


def forget(slug):
    """Close a cached connection, before its database is dropped or replaced."""
    conn = g.get("tb_db", {}).pop(slug, None)
    if conn is not None:
        conn.close()


def drop_study(slug):
    """Remove a study's database entirely. The next connect() starts it empty."""
    forget(slug)
    if is_postgres():
        with _pool().connection() as raw:
            raw.execute(f"DROP SCHEMA IF EXISTS {_quote(schema_name(slug))} CASCADE")
        return
    path = sqlite_path(slug)
    for suffix in ("", "-wal", "-shm"):
        p = path.with_name(path.name + suffix)
        if p.exists():
            p.unlink()


def study_exists(slug):
    if is_postgres():
        with _pool().connection() as raw:
            return raw.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s",
                               (schema_name(slug),)).fetchone() is not None
    return sqlite_path(slug).exists()


# ---------------------------------------------------------------- dialect helpers

def insert(conn, sql, params=()):
    """Run an INSERT into a table with an `id` column and return the new row's id."""
    if conn.dialect == "postgres":
        return conn.execute(sql.rstrip().rstrip(";") + " RETURNING id", params).fetchone()[0]
    return conn.execute(sql, params).lastrowid


def greatest(conn, a, b):
    """The larger of two SQL expressions: MAX(a, b) in SQLite, GREATEST(a, b) in PostgreSQL."""
    return f"GREATEST({a}, {b})" if conn.dialect == "postgres" else f"MAX({a}, {b})"


def ping():
    """True when the database answers. Used by /health."""
    try:
        if is_postgres():
            with _pool().connection(timeout=3) as raw:
                raw.execute("SELECT 1")
        return True
    except Exception:
        return False
