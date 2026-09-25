"""Projects must not share logins, admin access or data."""
from pathlib import Path

from .conftest import MINI_AB, PROTO, write_project


def two_projects(base):
    for slug, pattern in (("alpha", "^A\\d{2}$"), ("beta", "^B\\d{2}$")):
        write_project(base, slug, {"identity": {"mode": "code", "pattern": pattern}}, {
            "s": {"type": "survey", "questions": [{"id": "q", "type": "text"}]},
            "ab": {**MINI_AB, "requires": ["s"], "audience": {"identity_pattern": "1$"}},
        }, files={"p/a.html": PROTO, "p/b.html": PROTO, "p/style.css": "a{}"})


def test_logins_passcodes_and_data_are_separate(tmp_path, make_app):
    two_projects(tmp_path / "projects")
    app = make_app(tmp_path / "projects", ALPHA_PASSCODE="pa", BETA_PASSCODE="pb",
                   ALPHA_ADMIN_PASSCODE="aa", BETA_ADMIN_PASSCODE="ab")
    c = app.test_client()
    assert "does not match" in c.post("/alpha/", data={"identity": "A01", "passcode": "pb"}).get_data(as_text=True)
    assert "not recognised" in c.post("/alpha/", data={"identity": "B01", "passcode": "pa"}).get_data(as_text=True)
    assert c.post("/alpha/", data={"identity": "a01", "passcode": "pa"}).status_code == 302
    assert "A01" in c.get("/alpha/").get_data(as_text=True)
    assert "Participant code" in c.get("/beta/").get_data(as_text=True)  # still logged out of beta

    c.post("/alpha/admin/", data={"passcode": "aa"})
    assert c.get("/alpha/admin/participants").status_code == 200
    assert c.get("/beta/admin/participants").status_code == 302  # alpha admin is not beta admin
    assert c.post("/beta/admin/", data={"passcode": "aa"}).status_code == 200  # wrong project's code refused

    # Participant data lives in one file per project, so only the project that was used has one.
    # _system.db is the shared file for hashed passcodes, accounts and moderation, never answers.
    data = Path(app.config["DATA_DIR"])
    assert sorted(p.name for p in data.glob("*.db") if p.name != "_system.db") == ["alpha.db"]


def test_audience_hides_module_and_blocks_direct_access(tmp_path, make_app):
    two_projects(tmp_path / "projects")
    app = make_app(tmp_path / "projects", ALPHA_PASSCODE="pa")
    c = app.test_client()
    c.post("/alpha/", data={"identity": "A02", "passcode": "pa"})
    assert "Mini AB" not in c.get("/alpha/").get_data(as_text=True)
    assert c.get("/alpha/m/ab/").status_code == 404
    c2 = app.test_client()
    c2.post("/alpha/", data={"identity": "A01", "passcode": "pa"})
    assert "Mini AB" in c2.get("/alpha/").get_data(as_text=True)


def test_superadmin_opens_every_project(tmp_path, make_app):
    two_projects(tmp_path / "projects")
    app = make_app(tmp_path / "projects", SUPERADMIN_PASSCODE="root")
    c = app.test_client()
    assert "All projects" in c.get("/admin/").get_data(as_text=True) and "/alpha/admin/" not in c.get("/admin/").get_data(as_text=True)
    c.post("/admin/", data={"passcode": "root"})
    assert "/alpha/admin/" in c.get("/admin/").get_data(as_text=True)
    assert c.get("/alpha/admin/").status_code == 200 and c.get("/beta/admin/").status_code == 200


def test_missing_passcode_env_fails_closed(tmp_path, make_app):
    two_projects(tmp_path / "projects")
    app = make_app(tmp_path / "projects")
    c = app.test_client()
    r = c.post("/alpha/", data={"identity": "A01", "passcode": ""})
    assert "not open yet" in r.get_data(as_text=True)
    assert "ALPHA_ADMIN_PASSCODE" in c.post("/alpha/admin/", data={"passcode": ""}).get_data(as_text=True)


def test_code_parity_order(tmp_path, make_app):
    base = tmp_path / "projects"
    write_project(base, "cp", {"identity": {"mode": "code", "pattern": "^P\\d{2}$"}, "access": "open"},
                  {"ab": {**MINI_AB, "order": "code_parity"}}, files={"p/a.html": PROTO, "p/b.html": PROTO})
    app = make_app(base)
    from testbench import storage
    for code, expected in (("P01", ["A", "B"]), ("P02", ["B", "A"]), ("P03", ["A", "B"])):
        c = app.test_client()
        c.post("/cp/", data={"identity": code})
        c.get("/cp/m/ab/")
    with app.app_context():
        conn = storage.connect("cp")
        rows = conn.execute("SELECT p.identity, s.state FROM sessions s JOIN participants p ON p.id = s.participant_id").fetchall()
        got = {r["identity"]: storage.loads(r["state"])["order"] for r in rows}
    assert got == {"P01": ["A", "B"], "P02": ["B", "A"], "P03": ["A", "B"]}


def test_broken_edit_keeps_last_good_config(tmp_path, make_app):
    two_projects(tmp_path / "projects")
    app = make_app(tmp_path / "projects", ALPHA_PASSCODE="pa")
    reg = app.extensions["testbench"]
    survey = tmp_path / "projects" / "alpha" / "modules" / "s.yaml"
    survey.write_text("type: survey\ntitle: Renamed\nquestions: [{id: q, type: text}]\n")
    app.test_client().get("/alpha/")
    assert reg.get("alpha").module("s").title == "Renamed" and reg.last_error is None
    survey.write_text("type: survey\nquestions: [{id: q, type: nope}]\n")
    app.test_client().get("/alpha/")
    assert reg.get("alpha").module("s").title == "Renamed" and reg.last_error is not None
