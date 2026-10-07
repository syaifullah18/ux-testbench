"""First-click targets per variant inside ab_test."""
import csv
import io

import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES
from testbench.modules.ab_test import click_label, first_click_correct, paired_first_click_check

from .conftest import extract_json, write_project

FC_AB = {
    "type": "ab_test", "title": "FC", "order": "fixed", "ease_question": False, "preference": False,
    "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
    "tasks": [{"id": "find", "title": "Find", "prompt": "Find the NPWP mismatch",
               "first_click": {"A": ["Tab Data Pajak"], "B": ["Bagian Data Pajak", "Tombol Bandingkan Dokumen"]}}],
    "decision_rule": {"min_participants": 1},
}
FILES = {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"}


def test_label_matching():
    task = FC_AB["tasks"][0]
    assert click_label("button: Tab Data Pajak") == "Tab Data Pajak"
    assert click_label("div#x") == "div#x"
    assert first_click_correct(task, "A", "button: Tab Data Pajak") is True
    assert first_click_correct(task, "A", "a: tab data-pajak") is True        # same normalisation as answers
    assert first_click_correct(task, "A", "button: Tab Dokumen") is False
    assert first_click_correct(task, "A", None) is False
    assert first_click_correct(task, "B", "button: Tombol Bandingkan Dokumen") is True
    assert first_click_correct({"first_click": None}, "A", "x") is None


def test_config_rejects_unknown_variant(tmp_path):
    bad = dict(FC_AB, tasks=[{"id": "t", "prompt": "p", "first_click": {"Z": ["x"], "A": []}},
                             {"id": "u", "prompt": "p", "first_click": ["x"]}])
    write_project(tmp_path, "f", {"name": "F"}, {"ab": bad}, FILES)
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    text = "\n".join(err.value.problems)
    assert "first_click.Z: no variant" in text
    assert "first_click.A must list at least one" in text
    assert "tasks[1].first_click must map variant keys" in text


def row(label):
    return {"rows": {"find": {"first_click": label}}}


def test_paired_check_uses_sign_test():
    tasks = FC_AB["tasks"]
    pairs = [{"A": row("button: Wrong"), "B": row("button: Bagian Data Pajak")} for _ in range(6)]
    chk = paired_first_click_check(pairs, "A", "B", tasks)
    assert chk["value"] == "6/6" and chk["a"] == 0 and chk["b"] == 1
    assert chk["p_value"] == pytest.approx(0.03125) and chk["verdict"] == "evidence for"
    assert chk["counts"] is False
    assert paired_first_click_check(pairs[:3], "A", "B", tasks)["verdict"] == "not enough evidence yet"
    assert paired_first_click_check(pairs, "A", "B", [{"id": "find", "first_click": {"A": ["x"]}}]) is None


def run_person(app, first_clicks):
    c = app.test_client()
    c.post("/f/", data={"action": "start", "consent": "1"})
    c.get("/f/m/ab/")
    c.post("/f/m/ab/s/intro")
    for n in (1, 2):
        c.post(f"/f/m/ab/s/brief{n}")
        cfg = extract_json(c.get(f"/f/m/ab/s/tasks{n}").get_data(as_text=True), "TASKS")
        r = c.post(cfg["submitUrl"].replace("__T__", "find"),
                   json={"answer": {"answer": "x"}, "metrics": {"time_ms": 1000, "click_path": [first_clicks[n - 1]]}})
        assert r.status_code == 200, r.get_json()
    c.get(r.get_json()["next"])


def test_report_and_export(projects_dir, make_app):
    write_project(projects_dir, "f", {"name": "F", "locale": "en", "status": "live", "identity": "anonymous",
                                      "access": "open"}, {"ab": FC_AB}, FILES)
    app = make_app(projects_dir, F_ADMIN_PASSCODE="adm")
    run_person(app, ["button: Tab Dokumen", "button: Bagian Data Pajak"])
    run_person(app, ["button: Tab Data Pajak", "div"])
    admin = app.test_client()
    admin.post("/f/admin/", data={"passcode": "adm"})
    html = admin.get("/f/admin/m/ab/").get_data(as_text=True)
    assert "Clicked the right place first" in html
    assert "50% on target" in html and "“Tab Dokumen” (1)" in html
    text = admin.get("/f/admin/m/ab/export.csv").get_data(as_text=True).lstrip("﻿")
    table = list(csv.DictReader(io.StringIO(text)))
    got = sorted((r["variant"], r["first_click_label"], r["first_click_correct"]) for r in table)
    assert got == [("A", "Tab Data Pajak", "1"), ("A", "Tab Dokumen", "0"),
                   ("B", "Bagian Data Pajak", "1"), ("B", "div", "0")]
