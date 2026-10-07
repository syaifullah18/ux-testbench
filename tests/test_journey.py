"""journey_test: compare a current flow with a proposed one, step by step."""
import csv
import io

import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES
from testbench.modules.journey_test import handoffs

from .conftest import extract_json, write_project

JOURNEY = {
    "type": "journey_test", "title": "Vendor update to approval", "order": "fixed",
    "variants": {
        "current": {"label": "Current flow", "cycle_time_hours": 48, "steps": [
            {"id": "submit", "role": "vendor", "file": "cur/submit.html", "goal": "submitted"},
            {"id": "assign", "role": "assignor", "file": "cur/assign.html", "goal": "assigned"},
            {"id": "verify", "role": "verificator", "file": "cur/verify.html", "goal": "verified"},
            {"id": "approve", "role": "approver", "file": "cur/approve.html", "goal": "approved"}]},
        "proposed": {"label": "Proposed flow", "steps": [
            {"id": "submit", "role": "vendor", "file": "new/submit.html", "goal": "submitted"},
            {"id": "verify", "role": "verificator", "file": "new/verify.html", "goal": "verified",
             "failure": ["rejected"]}]},
    },
    "baseline": "current", "decision_rule": {"planned_participants": 2},
}
FILES = {f"{d}/{n}.html": f"<h1>{d} {n}</h1><button data-testbench-goal='{g}'>Done</button>"
         for d, steps in (("cur", [("submit", "submitted"), ("assign", "assigned"), ("verify", "verified"),
                                   ("approve", "approved")]),
                          ("new", [("submit", "submitted"), ("verify", "verified")]))
         for n, g in steps}


def setup(projects_dir, make_app, conf=JOURNEY):
    write_project(projects_dir, "jt", {"name": "JT", "locale": "en", "status": "live", "identity": "anonymous",
                                       "access": "open"}, {"flow": conf}, FILES)
    app = make_app(projects_dir, JT_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/jt/admin/", data={"passcode": "adm"})
    return app, admin


def test_structure_and_validation(tmp_path):
    steps = JOURNEY["variants"]["current"]["steps"]
    assert handoffs([dict(s) for s in steps]) == 3
    assert handoffs([{"role": "a"}, {"role": "a"}, {"role": "b"}]) == 1
    bad = dict(JOURNEY, roles_played_by="per_role", variants={
        "current": {"steps": [{"id": "x", "role": "bad role!", "file": "cur/missing.html", "goal": "ok"}]},
        "proposed": {"steps": [], "cycle_time_hours": -1}})
    write_project(tmp_path, "jt", {"name": "J"}, {"flow": bad}, FILES)
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    text = "\n".join(err.value.problems)
    for expected in ("only same_participant is supported", "role: 'bad role!'", "cur/missing.html' does not exist",
                     "variants.proposed.steps must list at least one step", "cycle_time_hours must be a positive"):
        assert expected in text


def test_structural_report_needs_no_participants(projects_dir, make_app):
    app, admin = setup(projects_dir, make_app)
    html = admin.get("/jt/admin/m/flow/").get_data(as_text=True)
    assert "Flow structure" in html
    cur = html.split("Current flow (current)", 1)[1].split("</tr>", 1)[0]
    assert '<td class="num text-right">4</td>' in cur and '<td class="num text-right">3</td>' in cur
    assert "vendor, assignor, verificator, approver" in cur and "48 h" in cur
    new = html.split("Proposed flow (proposed)", 1)[1].split("</tr>", 1)[0]
    assert '<td class="num text-right">2</td>' in new and '<td class="num text-right">1</td>' in new


def run(app, step_times, outcome="verified"):
    c = app.test_client()
    c.post("/jt/", data={"action": "start", "consent": "1"})
    c.get("/jt/m/flow/")
    c.post("/jt/m/flow/s/intro")
    for n, variant in ((1, "current"), (2, "proposed")):
        c.post(f"/jt/m/flow/s/brief{n}")
        cfg = extract_json(c.get(f"/jt/m/flow/s/tasks{n}").get_data(as_text=True), "TASKS")
        assert [t["fields"] for t in cfg["tasks"]] == [[]] * len(cfg["tasks"])     # goals grade every step
        for i, t in enumerate(cfg["tasks"]):
            page = c.get(t["frameUrl"]).get_data(as_text=True)
            assert f"{'cur' if variant == 'current' else 'new'} {t['id']}" in page   # each step serves its own file
            goal = JOURNEY["variants"][variant]["steps"][i]["goal"]
            if variant == "proposed" and t["id"] == "verify":
                goal = outcome
            ms = step_times[variant]
            r = c.post(cfg["submitUrl"].replace("__T__", t["id"]),
                       json={"answer": {}, "metrics": {"time_ms": ms + 500,
                                                       "events": [{"kind": "goal", "value": goal, "t_ms": ms}]}})
            assert r.status_code == 200, r.get_json()
    c.get(r.get_json()["next"])


def test_runs_compare_totals_and_export(projects_dir, make_app):
    app, admin = setup(projects_dir, make_app)
    run(app, {"current": 30000, "proposed": 20000})
    run(app, {"current": 40000, "proposed": 20000}, outcome="rejected")
    html = admin.get("/jt/admin/m/flow/").get_data(as_text=True)
    assert "Proposed flow against Current flow" in html and "2 of 2 people finished faster with Proposed flow" in html
    # Median saved per run: current totals 120 s and 160 s, proposed 40 s each -> 100 s.
    assert "Typical time saved: 1m 40s per run" in html
    assert "0.06% of the 48 hours the whole process takes" in html
    assert "Current flow 100%, Proposed flow 50%" in html          # one participant rejected at verify
    text = admin.get("/jt/admin/m/flow/export.csv").get_data(as_text=True).lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 2 * (4 + 2)
    rejected = [r for r in rows if r["variant"] == "proposed" and r["step"] == "verify" and r["goal_outcome"] == "failure"]
    assert len(rejected) == 1 and rejected[0]["role"] == "verificator" and rejected[0]["time_s"] == "20.0"


def test_preflight_checks_each_step(projects_dir, make_app):
    from testbench import preflight
    app, admin = setup(projects_dir, make_app, dict(JOURNEY, variants=dict(JOURNEY["variants"], proposed={
        "label": "P", "steps": [{"id": "submit", "role": "vendor", "file": "new/submit.html", "goal": "sent"}]})))
    with app.app_context():
        from testbench.web import get_project
        found = preflight.run(get_project("jt"))
    assert [(f["variant"], f["message"].split("'")[1]) for f in found if f["message"].startswith("Goal")] == \
        [("proposed/submit", "sent")]


def test_studio_sends_journey_to_yaml_editor(projects_dir, make_app):
    studio = projects_dir.parent / "data" / "projects"
    write_project(studio, "jt", {"name": "JT", "locale": "en", "status": "draft", "identity": "anonymous",
                                 "access": "open"}, {"flow": JOURNEY}, FILES)
    app = make_app(projects_dir, JT_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/jt/admin/", data={"passcode": "adm"})
    r = admin.get("/jt/admin/studio/modules/flow")
    assert r.status_code == 302 and "studio/edit" in r.location and "modules/flow.yaml" in r.location.replace("%2F", "/")


def test_flows_lock_when_live(projects_dir, make_app):
    import yaml
    from testbench import locks, storage
    studio = projects_dir.parent / "data" / "projects"
    pdir = write_project(studio, "jt", {"name": "JT", "locale": "en", "status": "draft", "identity": "anonymous",
                                        "access": "open"}, {"flow": JOURNEY}, FILES)
    app = make_app(projects_dir, JT_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/jt/admin/", data={"passcode": "adm"})
    admin.post("/jt/admin/status", data={"status": "live"})
    with app.app_context():
        lock = locks.get(storage.connect("jt"), "flow")
    assert lock["fields"]["variants"]["proposed"][1]["goals"] == {"success": ["verified"], "failure": ["rejected"]}
    changed = yaml.safe_load(yaml.safe_dump(JOURNEY))
    changed["variants"]["proposed"]["steps"][1]["goal"] = "checked"
    r = admin.post("/jt/admin/studio/edit", data={"file": "modules/flow.yaml", "action": "save",
                                                  "content": yaml.safe_dump(changed)})
    assert "analysis rules are locked" in r.get_data(as_text=True)
    assert "Rules fixed before testing" in admin.get("/jt/admin/m/flow/").get_data(as_text=True)


def test_studio_creates_a_journey_module(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    c.post("/admin/projects/new", data={"slug": "jn", "name": "JN", "source": "blank", "locale": "en",
                                        "identity": "code", "access": "passcode"})
    assert 'value="journey_test"' in c.get("/jn/admin/studio/").get_data(as_text=True)
    r = c.post("/jn/admin/studio/modules/new", data={"id": "flow", "title": "Flows", "type": "journey_test"})
    assert r.status_code == 302 and "error" not in r.location, r.location
    assert "Flow structure" in c.get("/jn/admin/m/flow/").get_data(as_text=True)
