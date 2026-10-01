from testbench.modules.first_click import FirstClick
from testbench.config import Project, Module


class DummyProject:
    def __init__(self):
        self.slug = "test"
        self.dir = "/"
        self.modules = {}

class DummyModule:
    def __init__(self, p, conf):
        self.project = p
        self.id = "fc"
        self.conf = conf

def test_first_click_validation():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": [
        {"id": "t1", "prompt": "Click logo", "image": "logo.png"}
    ], "post": [{"id": "q1", "type": "text", "label": "Q"}]})
    fc = FirstClick(m)
    
    class DummyScope:
        def __init__(self):
            self.errors = []
        def add(self, err):
            self.errors.append(err)
            
    scope = DummyScope()
    out = fc.validate(m.conf, scope)
    assert not scope.errors
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["image"] == "logo.png"

def test_first_click_steps():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": [{"id": "t1"}, {"id": "t2"}], "post": [{"id": "q"}]})
    fc = FirstClick(m)
    assert fc.steps({}) == ["t1", "t2", "post1", "post2"]


def test_first_click_validation_missing_prompt_or_image():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": [
        {"id": "t1"}  # missing both prompt and image
    ]})
    fc = FirstClick(m)

    class DummyScope:
        def __init__(self):
            self.errors = []
        def add(self, err):
            self.errors.append(err)

    scope = DummyScope()
    out = fc.validate(m.conf, scope)
    assert any("prompt" in err for err in scope.errors)
    assert any("image" in err for err in scope.errors)
    assert len(out["tasks"]) == 1
    assert out["tasks"][0]["prompt"] == ""
    assert out["tasks"][0]["image"] == ""


def test_first_click_validation_non_dict_task():
    p = DummyProject()
    m = DummyModule(p, {"type": "first_click", "tasks": ["not_a_dict"]})
    fc = FirstClick(m)

    class DummyScope:
        def __init__(self):
            self.errors = []
        def add(self, err):
            self.errors.append(err)

    scope = DummyScope()
    out = fc.validate(m.conf, scope)
    assert any("must be a mapping" in err for err in scope.errors)




# ---------------------------------------------------------------- end to end, with clicks

import json

from testbench import storage
from tests.conftest import write_project

FC = {"type": "first_click", "title": "FC", "tasks": [
    {"id": "home", "prompt": "Find home", "image": "prototypes/shot.png"},
    {"id": "returns", "prompt": "Find returns", "image": "prototypes/shot.png", "clicks": "all"},
]}


def fc_app(projects_dir, make_app, conf=FC):
    write_project(projects_dir, "fc", {"name": "FC", "access": "open", "identity": {"mode": "anonymous"}}, {"fc": conf},
                  files={"prototypes/shot.png": "PNG"})
    return make_app(projects_dir, FC_ADMIN_PASSCODE="adm")


def participant(app, first, multi):
    c = app.test_client()
    r = c.post("/fc/", data={"action": "start", "consent": "1"})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    c.get("/fc/m/fc/")
    r = c.post("/fc/m/fc/s/t1", data={"x": first[0], "y": first[1], "w": 1000, "h": 800, "time_ms": 1200,
                                      "viewport_w": 1280, "viewport_h": 800})
    assert r.status_code == 302
    r = c.post("/fc/m/fc/s/t2", data={"w": 1000, "h": 800, "viewport_w": 1280, "viewport_h": 800,
                                      "clicks": json.dumps([{"x": x, "y": y, "t_ms": 500 * (i + 1)}
                                                            for i, (x, y) in enumerate(multi)])})
    assert r.status_code == 302
    return c


def admin(app):
    a = app.test_client()
    a.post("/fc/admin/", data={"passcode": "adm"})
    return a


def test_clicks_mode_validates_and_defaults_to_first():
    class Scope(list):
        add = list.append
    fc = FirstClick(DummyModule(DummyProject(), {}))
    scope = Scope()
    out = fc.validate({"tasks": [{"id": "a", "prompt": "P", "image": "a.png"}]}, scope)
    assert not scope and out["tasks"][0]["clicks"] == "first"
    out = fc.validate({"clicks": "all", "tasks": [{"id": "a", "prompt": "P", "image": "a.png"},
                                                  {"id": "b", "prompt": "P", "image": "b.png", "clicks": "first"}]}, scope)
    assert not scope and [t["clicks"] for t in out["tasks"]] == ["all", "first"]
    fc.validate({"clicks": "some", "tasks": [{"id": "a", "prompt": "P", "image": "a.png", "clicks": 3}]}, scope)
    assert len(scope) == 2


def test_both_modes_store_clicks_and_keep_the_answer_page(projects_dir, make_app):
    app = fc_app(projects_dir, make_app)
    participant(app, (100, 200), [(10, 20), (30, 40), (1500, 50)])
    with app.app_context():
        conn = storage.connect("fc")
        rows = storage.bulk_clicks(conn, [1])
        assert [(k["x"], k["y"]) for k in rows["t1"]] == [(100, 200)]
        assert [(k["x"], k["y"]) for k in rows["t2"]] == [(10, 20), (30, 40), (1000, 50)]   # clamped
        assert rows["t2"][0]["viewport_w"] == 1280 and rows["t2"][2]["t_ms"] == 1500
        page = storage.page_answers(conn, 1, "t2")
        assert (page["x"], page["y"], page["clicks"]) == (10, 20, 3)   # first click, as before


def test_export_has_a_row_per_click_only_for_clicks_all(projects_dir, make_app):
    app = fc_app(projects_dir, make_app)
    participant(app, (100, 200), [(10, 20), (30, 40), (50, 60)])
    csv = admin(app).get("/fc/admin/m/fc/export.csv").get_data(as_text=True).lstrip("\ufeff").strip().splitlines()
    assert csv[0].startswith("participant,task,click,x,y,w,h,time_ms")
    assert len(csv) == 1 + 1 + 3
    assert csv[1].split(",")[1:4] == ["home", "", "100"]
    assert [line.split(",")[2:5] for line in csv[2:]] == [["1", "10", "20"], ["2", "30", "40"], ["3", "50", "60"]]


def test_export_header_is_unchanged_when_no_task_takes_every_click(projects_dir, make_app):
    conf = {**FC, "tasks": [FC["tasks"][0]]}
    app = fc_app(projects_dir, make_app, conf)
    c = app.test_client()
    c.post("/fc/", data={"action": "start", "consent": "1"})
    c.get("/fc/m/fc/")
    c.post("/fc/m/fc/s/t1", data={"x": 1, "y": 2, "w": 10, "h": 10, "time_ms": 5})
    csv = admin(app).get("/fc/admin/m/fc/export.csv").get_data(as_text=True).lstrip("\ufeff").splitlines()
    assert csv[0].startswith("participant,task,x,y,w,h,time_ms")


def test_report_shows_dots_below_the_minimum_and_a_grid_above(projects_dir, make_app):
    app = fc_app(projects_dir, make_app)
    for i in range(4):
        participant(app, (100 + i, 200), [(10, 20)])
    body = admin(app).get("/fc/admin/m/fc/").get_data(as_text=True)
    assert "data-heatmap" in body and "heatmap.js" in body
    assert "Fewer than 5 participants" in body and '"grid": null' in body
    participant(app, (500, 400), [(10, 20)])
    body = admin(app).get("/fc/admin/m/fc/").get_data(as_text=True)
    assert "Fewer than 5 participants" not in body
    assert '"grid": {' in body and "5 participants, 5 clicks" in body
    assert "&lt;section" not in body


def test_answers_saved_before_the_clicks_table_still_reach_the_heatmap(projects_dir, make_app):
    app = fc_app(projects_dir, make_app)
    participant(app, (100, 200), [(10, 20)])
    with app.app_context():
        conn = storage.connect("fc")
        conn.execute("DELETE FROM clicks")
        conn.commit()
    body = admin(app).get("/fc/admin/m/fc/").get_data(as_text=True)
    assert "1 participants, 1 clicks" in body
