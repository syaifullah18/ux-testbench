"""Error and rework metrics from prototype events: back-navigation, misclicks, errors, lostness."""
import csv
import io
import math

import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES
from testbench.modules.ab_test import rework

from .conftest import extract_json, write_project


def ev(kind, value, t=0):
    return {"kind": kind, "value": value, "t_ms": t}


def pages(*names):
    return [ev("page", n) for n in names]


def test_back_navigation_and_lostness():
    task = {"optimal_steps": 3}
    rw = rework(task, pages("index", "index#list", "index#detail", "index#list", "index#detail", "index#done"))
    assert rw["pages"] == 6 and rw["back"] == 2
    # S = 6 visits, N = 4 distinct, R = 3: sqrt((4/6 - 1)^2 + (3/4 - 1)^2)
    assert rw["lostness"] == pytest.approx(math.sqrt((4 / 6 - 1) ** 2 + (3 / 4 - 1) ** 2))
    perfect = rework(task, pages("a", "b", "c"))
    assert perfect["lostness"] == 0 and perfect["back"] == 0


def test_repeats_in_a_row_are_one_visit_and_counts():
    events = pages("a", "a", "b") + [ev("miss", "div"), ev("miss", "p"), ev("error", "npwp"), ev("goal", "x")]
    rw = rework({}, events)
    assert rw == {"pages": 2, "back": 0, "lostness": None, "errors": 1, "misclicks": 2}
    assert rework({"optimal_steps": 2}, [])["lostness"] is None


def test_optimal_steps_validated(tmp_path):
    from .conftest import MINI_AB, PROTO
    bad = dict(MINI_AB, tasks=[dict(MINI_AB["tasks"][0], optimal_steps=0), dict(MINI_AB["tasks"][1], optimal_steps="3")])
    write_project(tmp_path, "rw", {"name": "R"}, {"ab": bad}, {"p/a.html": PROTO, "p/b.html": PROTO})
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    text = "\n".join(err.value.problems)
    assert "tasks[0].optimal_steps must be a whole number" in text and "tasks[1].optimal_steps" in text


AB = {"type": "ab_test", "title": "RW", "order": "fixed", "ease_question": False, "preference": False,
      "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
      "tasks": [{"id": "t", "prompt": "Find it", "optimal_steps": 2}]}


def person(app, events_by_variant):
    c = app.test_client()
    c.post("/rw/", data={"action": "start", "consent": "1"})
    c.get("/rw/m/ab/")
    c.post("/rw/m/ab/s/intro")
    for n, variant in ((1, "A"), (2, "B")):
        c.post(f"/rw/m/ab/s/brief{n}")
        cfg = extract_json(c.get(f"/rw/m/ab/s/tasks{n}").get_data(as_text=True), "TASKS")
        r = c.post(cfg["submitUrl"].replace("__T__", "t"),
                   json={"answer": {"answer": "x"}, "metrics": {"time_ms": 1000, "events": events_by_variant[variant]}})
        assert r.status_code == 200
    c.get(r.get_json()["next"])


def test_report_and_export(projects_dir, make_app):
    write_project(projects_dir, "rw", {"name": "RW", "locale": "en", "status": "live", "identity": "anonymous",
                                       "access": "open"}, {"ab": AB}, {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"})
    app = make_app(projects_dir, RW_ADMIN_PASSCODE="adm")
    lost = pages("index", "index#x", "index", "index#x", "index#y") + [ev("miss", "div")] * 3 + [ev("error", "e")]
    direct = pages("index", "index#y")
    for _ in range(6):
        person(app, {"A": lost, "B": direct})
    admin = app.test_client()
    admin.post("/rw/admin/", data={"passcode": "adm"})
    html = admin.get("/rw/admin/m/ab/").get_data(as_text=True)
    assert "Signs of confusion" in html
    for heading in ("Went back to a page already seen", "Clicked something that does nothing", "Got an error message", "How lost they were"):
        assert heading in html
    assert "going back p=0.031, r=+1.00" in html     # all 6 people had fewer on B
    text = admin.get("/rw/admin/m/ab/export.csv").get_data(as_text=True).lstrip("﻿")
    rows = {r["variant"]: r for r in csv.DictReader(io.StringIO(text))}
    assert (rows["A"]["pages_visited"], rows["A"]["back_nav"], rows["A"]["misclicks"], rows["A"]["errors"]) == ("5", "2", "3", "1")
    assert rows["B"]["lostness"] == "0.0" and float(rows["A"]["lostness"]) > 0.4


def test_columns_hidden_without_events(projects_dir, make_app):
    write_project(projects_dir, "rw", {"name": "RW", "locale": "en", "status": "live", "identity": "anonymous",
                                       "access": "open"}, {"ab": AB}, {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"})
    app = make_app(projects_dir, RW_ADMIN_PASSCODE="adm")
    person(app, {"A": [], "B": []})
    admin = app.test_client()
    admin.post("/rw/admin/", data={"passcode": "adm"})
    assert "Signs of confusion" not in admin.get("/rw/admin/m/ab/").get_data(as_text=True)
