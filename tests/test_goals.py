"""Goal detection: a prototype reports that the participant reached an outcome."""
import csv
import io

import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES

from .conftest import extract_json, write_project

GOAL_AB = {
    "type": "ab_test", "title": "Goals",
    "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
    "ease_question": False,
    "tasks": [
        {"id": "decide", "title": "Decide", "prompt": "Finish the verification",
         "goals": {"success": ["return-to-vendor"], "failure": ["approve"]}},
        {"id": "both", "title": "Both", "prompt": "Type 42 and save",
         "fields": [{"id": "x", "label": "X"}], "accept": {"x": ["42"]}, "goals": {"success": ["saved"]},
         "end_on_goal": False},
    ],
}
FILES = {"p/a.html": "<button data-testbench-goal='approve'>Approve</button>", "p/b.html": "<p>B</p>"}


def start(projects_dir, make_app, conf=GOAL_AB):
    write_project(projects_dir, "g", {"name": "G", "locale": "en", "status": "live", "identity": "anonymous",
                                      "access": "open"}, {"ab": conf}, FILES)
    app = make_app(projects_dir, G_ADMIN_PASSCODE="adm")
    c = app.test_client()
    c.post("/g/", data={"action": "start", "consent": "1"})
    c.get("/g/m/ab/")
    c.post("/g/m/ab/s/intro")
    c.post("/g/m/ab/s/brief1")
    return app, c, extract_json(c.get("/g/m/ab/s/tasks1").get_data(as_text=True), "TASKS")


def goal(value, t_ms):
    return {"kind": "goal", "value": value, "t_ms": t_ms}


def rows(app):
    from testbench import storage
    with app.app_context():
        conn = storage.connect("g")
        return {r["task_id"]: dict(r) for r in conn.execute("SELECT * FROM task_results WHERE position = 1")}


def test_config_validates_goals(tmp_path):
    bad = dict(GOAL_AB, tasks=[
        {"id": "a", "prompt": "p", "goals": ["x"]},
        {"id": "b", "prompt": "p", "goals": {"failure": ["x"]}},
        {"id": "c", "prompt": "p", "goals": {"success": ["x y"]}},
        {"id": "d", "prompt": "p", "goals": {"success": ["x"], "failure": ["x"]}},
    ])
    write_project(tmp_path, "g", {"name": "G"}, {"ab": bad}, FILES)
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    problems = "\n".join(err.value.problems)
    assert "tasks[0].goals must be a mapping" in problems
    assert "tasks[1].goals.success must list at least one goal id" in problems
    assert "'x y' must be 1 to 80" in problems
    assert "cannot be both success and failure" in problems


def test_goal_only_task_has_no_fields_and_ends_on_goal(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    t = cfg["tasks"][0]
    assert t["fields"] == [] and t["end_on_goal"] is True
    assert t["goals"] == ["return-to-vendor", "approve"]
    sub = cfg["submitUrl"].replace("__T__", "decide")
    events = [goal("opened-tab", 1000), goal("return-to-vendor", 4200), goal("approve", 9000)]
    r = c.post(sub, json={"answer": {}, "ease": 0, "metrics": {"time_ms": 7000, "events": events}})
    assert r.status_code == 200, r.get_json()
    row = rows(app)["decide"]
    assert row["auto_pass"] == 1
    assert row["time_ms"] == 4200       # time ends at the first listed goal


def test_failure_goal_and_unlisted_goal(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    sub = cfg["submitUrl"].replace("__T__", "decide")
    c.post(sub, json={"answer": {}, "metrics": {"time_ms": 5000, "events": [goal("approve", 3000)]}})
    assert rows(app)["decide"]["auto_pass"] == 0


def test_unlisted_goal_never_decides(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    sub = cfg["submitUrl"].replace("__T__", "decide")
    c.post(sub, json={"answer": {}, "metrics": {"time_ms": 5000, "events": [goal("elsewhere", 3000)]}})
    row = rows(app)["decide"]
    assert row["auto_pass"] == 0 and row["time_ms"] == 5000


def test_answer_key_and_goal_must_both_pass(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    c.post(cfg["submitUrl"].replace("__T__", "decide"),
           json={"answer": {}, "metrics": {"events": [goal("return-to-vendor", 10)]}})
    sub = cfg["submitUrl"].replace("__T__", "both")
    c.post(sub, json={"answer": {"x": "42"}, "metrics": {"time_ms": 8000, "events": [goal("saved", 3000)]}})
    row = rows(app)["both"]
    assert row["auto_pass"] == 1
    assert row["time_ms"] == 8000       # end_on_goal: false keeps the participant's own finish


def test_answer_right_but_goal_missed_fails(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    c.post(cfg["submitUrl"].replace("__T__", "decide"), json={"answer": {}, "metrics": {}})
    c.post(cfg["submitUrl"].replace("__T__", "both"), json={"answer": {"x": "42"}, "metrics": {}})
    assert rows(app)["both"]["auto_pass"] == 0


def test_events_are_cleaned_and_never_shrink(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    met = cfg["metricsUrl"].replace("__T__", "decide")
    c.post(met, json={"events": [goal("a", 1), goal("b", 2), {"kind": "evil", "value": "x"}, "junk",
                                 goal("", 3), goal("x" * 200, 4)]})
    c.post(met, json={"events": [goal("a", 1)]})     # stale, shorter: ignored
    cfg2 = extract_json(c.get("/g/m/ab/s/tasks1").get_data(as_text=True), "TASKS")
    ev = cfg2["tasks"][0]["initial"]["events"]
    assert [e["value"] for e in ev] == ["a", "b", "x" * 80]   # resumed runner continues the list


def test_export_has_goal_columns(projects_dir, make_app):
    app, c, cfg = start(projects_dir, make_app)
    c.post(cfg["submitUrl"].replace("__T__", "decide"),
           json={"answer": {}, "metrics": {"events": [goal("return-to-vendor", 4200)]}})
    admin = app.test_client()
    admin.post("/g/admin/", data={"passcode": "adm"})
    text = admin.get("/g/admin/m/ab/export.csv").get_data(as_text=True)
    table = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    header, out = table[0], table[1:]
    row = dict(zip(header, next(r for r in out if r[6] == "decide")))
    assert row["goal_outcome"] == "success"
    assert row["goal_events"] == "return-to-vendor@4.2s"


def test_tasks_without_goals_keep_the_answer_field(projects_dir, make_app):
    conf = dict(GOAL_AB, tasks=[{"id": "t", "prompt": "Anything"}])
    _, _, cfg = start(projects_dir, make_app, conf)
    assert [f["id"] for f in cfg["tasks"][0]["fields"]] == ["answer"]
    assert cfg["tasks"][0]["goals"] == [] and cfg["tasks"][0]["end_on_goal"] is False


def test_studio_keeps_goal_settings():
    from testbench.studio import module_to_ui_data
    data = {"type": "ab_test", "variants": {"A": {"file": "p/a.html"}},
            "tasks": [{"id": "t", "prompt": "p", "goals": {"success": ["s"]}, "end_on_goal": False,
                       "first_click": {"A": ["Save"]}}]}
    ui = module_to_ui_data("ab", None, data)
    t = ui["tasks"][0]
    assert t["keep"] == {"goals": {"success": ["s"]}, "end_on_goal": False, "first_click": {"A": ["Save"]}}
    assert t["noFields"] is True
