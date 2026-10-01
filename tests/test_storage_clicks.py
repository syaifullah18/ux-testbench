"""The clicks table: reaching old project files, idempotent writes, and deletion."""
import re
import sqlite3

import pytest

from testbench import storage


@pytest.fixture
def app(projects_dir, make_app):
    return make_app(projects_dir)


def v1_file(path):
    """A project database as a v1 build left it: every table except clicks."""
    v1 = re.sub(r"CREATE TABLE IF NOT EXISTS clicks \(.*?\);\s*CREATE INDEX IF NOT EXISTS clicks_surface[^;]*;",
                "", storage.SCHEMA, flags=re.S)
    assert "TABLE IF NOT EXISTS clicks" not in v1
    conn = sqlite3.connect(path)
    conn.executescript(v1)
    conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO participants (identity, created_at, last_seen_at) VALUES ('P01', 'x', 'x')")
    conn.commit()
    conn.close()


def participant_with_session(conn, identity="P01"):
    conn.execute("INSERT OR IGNORE INTO participants (identity, created_at, last_seen_at) VALUES (?, 'x', 'x')",
                 (identity,))
    pid = conn.execute("SELECT id FROM participants WHERE identity = ?", (identity,)).fetchone()[0]
    conn.execute("INSERT INTO sessions (participant_id, module_id, step, started_at) VALUES (?, 'fc', 't1', 'x')",
                 (pid,))
    return pid, conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def point(seq, x=10, y=20, **kw):
    return {"seq": seq, "x": x, "y": y, "doc_w": 100, "doc_h": 200, **kw}


def test_an_existing_v1_database_gains_the_table_on_open(app):
    with app.app_context():
        path = storage.db_path("example")
        path.parent.mkdir(parents=True, exist_ok=True)
        v1_file(path)
        conn = storage.connect("example")
        assert conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 1   # data kept
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == 0
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        assert version == str(storage.SCHEMA_VERSION) == "2"


def test_the_same_payload_written_twice_is_stored_once(app):
    with app.app_context():
        conn = storage.connect("example")
        _, sid = participant_with_session(conn)
        payload = [point(0), point(1, x=50)]
        assert storage.save_clicks(conn, sid, "t1", payload) == 2
        assert storage.save_clicks(conn, sid, "t1", payload) == 0
        assert storage.save_clicks(conn, sid, "t1", payload + [point(2)]) == 1
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == 3


def test_bad_points_are_dropped_and_coordinates_clamped(app):
    with app.app_context():
        conn = storage.connect("example")
        _, sid = participant_with_session(conn)
        stored = storage.save_clicks(conn, sid, "t1", [
            point(0, x=-5, y=900),              # clamped into the document
            point(1, x=1.5),                    # not an integer
            point(2, x="12"),                   # not an integer either
            point(3, doc_w=0),                  # no document
            {"x": 1, "y": 1},                   # no seq
            point(storage.CLICKS_MAX),          # over the cap
            "junk",
            point(4, x=True),                   # a boolean is not a coordinate
        ])
        assert stored == 1
        row = conn.execute("SELECT x, y FROM clicks").fetchone()
        assert (row["x"], row["y"]) == (0, 200)
        assert storage.save_clicks(conn, sid, "t1", "not a list") == 0


def test_the_cap_holds_however_many_points_arrive(app):
    with app.app_context():
        conn = storage.connect("example")
        _, sid = participant_with_session(conn)
        storage.save_clicks(conn, sid, "t1", [point(i) for i in range(storage.CLICKS_MAX + 50)])
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == storage.CLICKS_MAX


def test_replace_swaps_the_whole_list(app):
    with app.app_context():
        conn = storage.connect("example")
        _, sid = participant_with_session(conn)
        storage.save_clicks(conn, sid, "t1", [point(0), point(1), point(2)])
        storage.save_clicks(conn, sid, "t1", [point(0, x=99)], replace=True)
        rows = conn.execute("SELECT x FROM clicks").fetchall()
        assert [r["x"] for r in rows] == [99]


def test_bulk_clicks_groups_by_surface_in_order(app):
    with app.app_context():
        conn = storage.connect("example")
        _, a = participant_with_session(conn, "P01")
        _, b = participant_with_session(conn, "P02")
        storage.save_clicks(conn, a, "t1", [point(1, x=2), point(0, x=1)])
        storage.save_clicks(conn, b, "t2", [point(0, x=3)])
        out = storage.bulk_clicks(conn, [a, b])
        assert [p["x"] for p in out["t1"]] == [1, 2]
        assert out["t2"][0]["session_id"] == b
        assert list(storage.bulk_clicks(conn, [a, b], surface="t2")) == ["t2"]
        assert storage.bulk_clicks(conn, []) == {}


def test_reset_and_participant_deletion_remove_clicks(app):
    with app.app_context():
        conn = storage.connect("example")
        pid, sid = participant_with_session(conn, "P01")
        _, other = participant_with_session(conn, "P02")
        storage.save_clicks(conn, sid, "t1", [point(0)])
        storage.save_clicks(conn, other, "t1", [point(0)])
        conn.commit()
        conn.execute("DELETE FROM participants WHERE id = ?", (pid,))   # what admin.delete_participant runs
        assert conn.execute("SELECT COUNT(*) FROM clicks WHERE session_id = ?", (sid,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == 1
        storage.reset("example")
        assert conn.execute("SELECT COUNT(*) FROM clicks").fetchone()[0] == 0
