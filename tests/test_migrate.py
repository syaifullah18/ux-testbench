"""Moving a SQLite instance onto PostgreSQL keeps every row and every id."""
import pytest

from testbench import create_app, db, migrate, storage, users
from .conftest import on_postgres


def sqlite_instance(tmp_path, projects_dir):
    """A SQLite instance with participants, sessions, answers, clicks and an account."""
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "DATA_DIR": str(tmp_path / "old"),
                      "PROJECTS_DIRS": [str(projects_dir)], "DATABASE_URL": None})
    with app.app_context():
        conn = storage.connect("example")
        ids = []
        for i in range(3):
            pid = db.insert(conn, "INSERT INTO participants (identity, created_at, last_seen_at) VALUES (?, ?, ?)",
                            (f"P{i}", "2026-01-01", "2026-01-01"))
            sid = db.insert(conn, "INSERT INTO sessions (participant_id, module_id, step, started_at) "
                                  "VALUES (?, 'm', 's', 't')", (pid,))
            storage.save_page(conn, sid, "page1", {"q": i})
            storage.save_clicks(conn, sid, "t1", [{"seq": 0, "x": i, "y": i, "doc_w": 10, "doc_h": 10}])
            ids.append((pid, sid))
        # A gap in the ids, as deletions leave in a real database.
        conn.execute("DELETE FROM participants WHERE id = ?", (ids[1][0],))
        conn.commit()
        users.create_user("owner@example.org", "Owner", "a-long-password")
    return tmp_path / "old", [ids[0], ids[2]]


@on_postgres
def test_every_row_and_id_arrives_and_new_rows_do_not_collide(tmp_path, projects_dir, make_app):
    old, kept = sqlite_instance(tmp_path, projects_dir)
    app = make_app(projects_dir)
    with app.app_context():
        results = migrate.sqlite_to_postgres(old, log=lambda *_: None)
        assert results == {"example": "ok", "platform": "ok"}

        conn = storage.connect("example")
        assert [r[0] for r in conn.execute("SELECT id FROM participants ORDER BY id")] == [p for p, _ in kept]
        assert conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 2     # cascade kept them in step
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == 2
        assert storage.loads(conn.execute("SELECT data FROM answers ORDER BY session_id").fetchone()[0]) == {"q": 0}

        # The sequence moved past the copied ids, so the next participant gets a fresh one.
        new_id = db.insert(conn, "INSERT INTO participants (identity, created_at, last_seen_at) VALUES ('N', 't', 't')")
        assert new_id > max(p for p, _ in kept)

        user = users.get_user_by_email("owner@example.org")
        assert user is not None and users.verify_password(user, "a-long-password")


@on_postgres
def test_running_it_twice_skips_what_is_already_there(tmp_path, projects_dir, make_app):
    old, _ = sqlite_instance(tmp_path, projects_dir)
    app = make_app(projects_dir)
    with app.app_context():
        migrate.sqlite_to_postgres(old, log=lambda *_: None)
        again = migrate.sqlite_to_postgres(old, log=lambda *_: None)
        assert all(v.startswith("skipped") for v in again.values())
        assert storage.connect("example").execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 2


@on_postgres
def test_dry_run_writes_nothing(tmp_path, projects_dir, make_app):
    old, _ = sqlite_instance(tmp_path, projects_dir)
    app = make_app(projects_dir)
    with app.app_context():
        assert migrate.sqlite_to_postgres(old, dry_run=True, log=lambda *_: None) == {
            "example": "dry-run", "platform": "dry-run"}
        assert not db.study_exists("example")


def test_it_refuses_to_run_without_postgres(tmp_path, projects_dir):
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "DATA_DIR": str(tmp_path / "data"),
                      "DATABASE_URL": None, "PROJECTS_DIRS": [str(projects_dir)]})
    with app.app_context(), pytest.raises(SystemExit):
        migrate.sqlite_to_postgres(tmp_path / "data")
