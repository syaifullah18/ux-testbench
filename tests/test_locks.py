"""Pre-registered rules lock when a study goes live; pilots are left out; interim labels."""
import csv
import io

import yaml

from testbench import locks, storage

from .conftest import extract_json, write_project

AB = {
    "type": "ab_test", "title": "Lock", "order": "fixed", "ease_question": False, "preference": False,
    "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
    "tasks": [{"id": "t", "title": "T", "prompt": "Type 42", "fields": [{"id": "x", "label": "X"}],
               "accept": {"x": ["42"]}}],
    "decision_rule": {"planned_participants": 3},
}
FILES = {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"}


def setup(projects_dir, make_app, status="draft"):
    # The Studio folder (DATA_DIR/projects), so the admin site may edit the study.
    studio = projects_dir.parent / "data" / "projects"
    pdir = write_project(studio, "lk", {"name": "L", "locale": "en", "status": status, "identity": "anonymous",
                                              "access": "open"}, {"ab": AB}, FILES)
    app = make_app(projects_dir, LK_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/lk/admin/", data={"passcode": "adm"})
    return pdir, app, admin


def person(app, b_time=1000, c=None):
    """A participant; pass the admin client to pilot a draft study as the researcher."""
    c = c or app.test_client()
    c.post("/lk/", data={"action": "start", "consent": "1"})
    c.get("/lk/m/ab/")
    c.post("/lk/m/ab/s/intro")
    for n in (1, 2):
        c.post(f"/lk/m/ab/s/brief{n}")
        cfg = extract_json(c.get(f"/lk/m/ab/s/tasks{n}").get_data(as_text=True), "TASKS")
        r = c.post(cfg["submitUrl"].replace("__T__", "t"),
                   json={"answer": {"x": "42"}, "metrics": {"time_ms": 5000 if n == 1 else b_time}})
        assert r.status_code == 200, r.get_json()
    c.get(r.get_json()["next"])


def go_live(admin):
    return admin.post("/lk/admin/status", data={"status": "live"})


def lock_row(app):
    with app.app_context():
        return locks.get(storage.connect("lk"), "ab")


def test_going_live_locks_and_pilots_are_hidden(projects_dir, make_app):
    pdir, app, admin = setup(projects_dir, make_app)
    person(app, c=admin)                         # the researcher pilots the draft
    assert lock_row(app) is None
    go_live(admin)
    lock = lock_row(app)
    assert lock and lock["fields"]["tasks"][0]["accept"] == {"x": ["42"]}
    assert lock["fields"]["decision_rule"]["min_participants"] == 3
    html = admin.get("/lk/admin/m/ab/").get_data(as_text=True)
    assert "Locked since" in html and "trial run</span> from before the lock left out" in html
    html = admin.get("/lk/admin/m/ab/?pilot=1").get_data(as_text=True)
    assert "Including 1 <span class=\"term\"" in html
    person(app)
    text = admin.get("/lk/admin/m/ab/export.csv").get_data(as_text=True).lstrip("﻿")
    assert sorted(r["pilot"] for r in csv.DictReader(io.StringIO(text))) == ["0", "0", "1", "1"]


def test_studio_rejects_changes_to_locked_fields(projects_dir, make_app):
    pdir, app, admin = setup(projects_dir, make_app)
    go_live(admin)
    changed = dict(AB, tasks=[dict(AB["tasks"][0], accept={"x": ["43"]})])
    r = admin.post("/lk/admin/studio/edit", data={"file": "modules/ab.yaml", "action": "save",
                                                  "content": yaml.safe_dump(changed)})
    html = r.get_data(as_text=True)
    assert "analysis rules are locked" in html and "tasks.t.accept" in html
    assert "43" not in (pdir / "modules" / "ab.yaml").read_text()
    # A wording change is not an analysis change.
    reworded = dict(AB, title="Lock (reworded)")
    r = admin.post("/lk/admin/studio/edit", data={"file": "modules/ab.yaml", "action": "save",
                                                  "content": yaml.safe_dump(reworded)})
    assert r.status_code == 302
    assert "reworded" in (pdir / "modules" / "ab.yaml").read_text()


def test_unlock_needs_reason_and_relock_starts_new_period(projects_dir, make_app):
    pdir, app, admin = setup(projects_dir, make_app)
    go_live(admin)
    person(app)
    admin.post("/lk/admin/m/ab/rules", data={"action": "unlock", "reason": ""})
    assert lock_row(app) is not None
    admin.post("/lk/admin/m/ab/rules", data={"action": "unlock", "reason": "Answer key typo"})
    assert lock_row(app) is None
    changed = dict(AB, tasks=[dict(AB["tasks"][0], accept={"x": ["42", "forty-two"]})])
    r = admin.post("/lk/admin/studio/edit", data={"file": "modules/ab.yaml", "action": "save",
                                                  "content": yaml.safe_dump(changed)})
    assert r.status_code == 302
    admin.post("/lk/admin/m/ab/rules", data={"action": "lock"})
    assert lock_row(app)["fields"]["tasks"][0]["accept"] == {"x": ["42", "forty-two"]}
    html = admin.get("/lk/admin/m/ab/").get_data(as_text=True)
    assert "Answer key typo" in html and "Lock history (3)" in html
    assert "trial run</span> from before the lock left out" in html   # the earlier period


def test_hand_edit_after_lock_withholds_verdict_and_fails_check(projects_dir, make_app):
    pdir, app, admin = setup(projects_dir, make_app)
    go_live(admin)
    for _ in range(6):                           # the exact sign test needs 6 to reach p < 0.05
        person(app, b_time=1000)
    html = admin.get("/lk/admin/m/ab/").get_data(as_text=True)
    assert "Yes, clearly better" in html and "Early sign" not in html
    data = yaml.safe_load((pdir / "modules" / "ab.yaml").read_text())
    data["baseline"] = "B"
    (pdir / "modules" / "ab.yaml").write_text(yaml.safe_dump(data))
    from testbench.web import registry
    with app.app_context():
        registry().refresh(force=True)
    html = admin.get("/lk/admin/m/ab/").get_data(as_text=True)
    assert "The setup changed after the lock (baseline)" in html
    assert "Early sign:" in html


def test_interim_labels_below_planned(projects_dir, make_app):
    pdir, app, admin = setup(projects_dir, make_app, status="live")
    admin.post("/lk/admin/m/ab/rules", data={"action": "lock"})
    person(app)
    html = admin.get("/lk/admin/m/ab/").get_data(as_text=True)
    assert "Early result" in html
    assert "Interim: 1 of 3 planned people" in admin.get("/lk/admin/results").get_data(as_text=True)


def test_non_ab_modules_never_lock(projects_dir):
    class M:
        type = "survey"
    assert locks.snapshot(M()) is None


def test_check_cli_reports_changed_rules(projects_dir, make_app, monkeypatch):
    pdir, app, admin = setup(projects_dir, make_app)
    go_live(admin)
    data = yaml.safe_load((pdir / "modules" / "ab.yaml").read_text())
    data["order"] = "random"
    (pdir / "modules" / "ab.yaml").write_text(yaml.safe_dump(data))
    from testbench.__main__ import lock_problems
    from testbench.config import load_all
    from testbench.modules import MODULE_TYPES
    monkeypatch.setenv("DATA_DIR", app.config["DATA_DIR"])
    problems = lock_problems({"lk": load_all(MODULE_TYPES, [pdir.parent])["lk"]})
    assert problems and "unlock the module to change order" in problems[0]
