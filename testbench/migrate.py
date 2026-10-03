"""Moving an existing SQLite instance onto PostgreSQL.

`python -m testbench migrate-to-postgres` reads every `<slug>.db` and `_system.db` in DATA_DIR
and copies it into its PostgreSQL schema:

- one transaction per database, so a study is either fully copied or not at all;
- ids are kept, and each identity sequence is moved past the largest copied id, so the next
  row the app inserts does not collide with a copied one;
- only columns present on both sides are copied, so a file written by an older version (with
  fewer columns) still migrates, and the new columns take their defaults;
- row counts are compared table by table before a database is reported as done;
- a database whose target already holds rows is skipped, so running it twice is safe;
- the SQLite files are never modified or deleted.
"""
import sqlite3
from pathlib import Path

from . import db

# Parents before children, so foreign keys are satisfied as rows arrive.
STUDY_ORDER = ["meta", "participants", "sessions", "answers", "task_results", "clicks", "audit_log"]
SYSTEM_ORDER = ["users", "auth_tokens", "sessions_auth", "memberships", "invitations", "reports",
                "project_flags", "platform_audit", "signup_events", "rate_events", "passcodes"]
BATCH = 500


def _source_tables(src):
    return [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]


def _columns_sqlite(src, table):
    return [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]


def _columns_pg(conn, table):
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position", (table,))]


def _has_identity(conn, table):
    return conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = ? AND column_name = 'id' AND is_identity = 'YES'", (table,)).fetchone() is not None


def _copy_one(src, conn, order):
    """Copy every table of one SQLite database into the schema `conn` points at. Returns
    {table: rows}. Raises when the target already holds rows, or when counts disagree."""
    tables = _source_tables(src)
    ordered = [t for t in order if t in tables] + sorted(t for t in tables if t not in order)
    target_tables = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}

    for table in ordered:
        if table in target_tables and table != "meta":
            if conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
                raise RuntimeError(f"target table {table} already has rows")

    copied = {}
    raw = conn.raw
    with raw.transaction():
        for table in ordered:
            if table not in target_tables:
                continue   # a table this version no longer has, or a stray one: nothing to copy into
            cols = [c for c in _columns_sqlite(src, table) if c in set(_columns_pg(conn, table))]
            if not cols:
                continue
            col_sql = ", ".join(f'"{c}"' for c in cols)
            if table == "meta":
                conn.execute('DELETE FROM "meta"')   # written by connect(); the source's wins
            insert = (f'INSERT INTO "{table}" ({col_sql}) VALUES ({", ".join("?" for _ in cols)})')
            rows = src.execute(f'SELECT {col_sql} FROM "{table}"')
            n = 0
            while True:
                batch = rows.fetchmany(BATCH)
                if not batch:
                    break
                conn.executemany(insert, [tuple(r) for r in batch])
                n += len(batch)
            copied[table] = n
            if "id" in cols and _has_identity(conn, table):
                conn.execute(f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), "
                             f'COALESCE((SELECT MAX(id) FROM "{table}"), 1), '
                             f'(SELECT MAX(id) FROM "{table}") IS NOT NULL)')
        for table, n in copied.items():
            got = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if got != n:
                raise RuntimeError(f"{table}: copied {n} rows but found {got}")
    return copied


def sqlite_to_postgres(data_dir, dry_run=False, log=print):
    """Migrate every SQLite database in data_dir. Call inside an app context whose DATABASE_URL
    points at PostgreSQL. Returns {name: "ok" | "skipped: …" | "failed: …"}."""
    from . import storage, users
    if not db.is_postgres():
        raise SystemExit("DATABASE_URL must point at PostgreSQL to migrate into it.")
    files = sorted(Path(data_dir).glob("*.db"))
    if not files:
        log(f"No SQLite databases in {data_dir}.")
        return {}
    results = {}
    for path in files:
        slug = path.stem
        is_system = slug == "_system"
        name = "platform" if is_system else slug
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            counts = {t: src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _source_tables(src)}
            if dry_run:
                log(f"{name}: would copy " + ", ".join(f"{t} {n}" for t, n in counts.items() if n))
                results[name] = "dry-run"
                continue
            conn = users.system_db() if is_system else storage.connect(slug)
            try:
                copied = _copy_one(src, conn, SYSTEM_ORDER if is_system else STUDY_ORDER)
            except RuntimeError as exc:
                if "already has rows" in str(exc):
                    results[name] = f"skipped: {exc}"
                    log(f"{name}: skipped ({exc})")
                    continue
                raise
            total = sum(copied.values())
            results[name] = "ok"
            log(f"{name}: copied {total} rows (" + ", ".join(f"{t} {n}" for t, n in copied.items() if n) + ")")
        except Exception as exc:
            results[name] = f"failed: {exc}"
            log(f"{name}: FAILED, nothing written for it ({exc})")
        finally:
            src.close()
    return results
