"""Standard questionnaires: SUS and UMUX-Lite presets with fixed wording and automatic scores."""
import csv
import io

import pytest

from testbench import questions as Q
from testbench.config import ConfigError, Problems, load_all
from testbench.modules import MODULE_TYPES

from .conftest import extract_json, write_project


def expand(raw, locale="en"):
    problems = Problems()
    return Q.normalize([raw], problems, "q", locale=locale), problems


def test_sus_expands_and_scores():
    (q,), problems = expand({"id": "sus", "preset": "sus"})
    assert not problems
    assert q["type"] == "matrix" and q["points"] == 5 and len(q["rows"]) == 10 and q["required"]
    assert q["rows"][0] == ("1", "I think that I would like to use this system frequently.")
    assert Q.preset_score(q, {str(i): 5 if i % 2 else 1 for i in range(1, 11)}) == 100
    assert Q.preset_score(q, {str(i): 1 if i % 2 else 5 for i in range(1, 11)}) == 0
    assert Q.preset_score(q, {str(i): 3 for i in range(1, 11)}) == 50
    assert Q.preset_score(q, {"1": 5}) is None                 # incomplete answers get no score
    assert Q.sus_grade(85) == "A+" and Q.sus_grade(68) == "C" and Q.sus_grade(40) == "F"


def test_umux_lite_scores_and_sus_equivalent():
    (q,), _ = expand({"id": "u", "preset": "umux_lite"})
    assert q["points"] == 7 and len(q["rows"]) == 2
    assert Q.preset_score(q, {"1": 7, "2": 7}) == 100
    assert Q.preset_score(q, {"1": 4, "2": 4}) == 50
    assert Q.umux_lite_to_sus(50) == pytest.approx(55.4)


def test_locale_and_translation_note():
    (q,), _ = expand({"id": "sus", "preset": "sus"}, locale="id")
    assert q["preset_locale"] == "id" and q["labels"][0] == "Sangat tidak setuju"
    assert "Sharfina & Santoso" in q["preset_note"]
    (q,), _ = expand({"id": "sus", "preset": "sus", "locale": "en"}, locale="id")
    assert q["preset_locale"] == "en"
    (q,), _ = expand({"id": "sus", "preset": "sus"}, locale="fr")   # no wording: falls back to English
    assert q["preset_locale"] == "en"


def test_presets_cannot_be_edited():
    out, problems = expand({"id": "sus", "preset": "sus", "rows": ["a"], "points": 7})
    assert out == [] and "preset sus has fixed wording and scale; remove points, rows" in problems[0]
    out, problems = expand({"id": "x", "preset": "nps"})
    assert out == [] and "preset must be one of" in problems[0]


def test_flatten_adds_score_column():
    (q,), _ = expand({"id": "sus", "preset": "sus"})
    cols = Q.flatten([q], {"sus": {str(i): 3 for i in range(1, 11)}})
    assert cols["sus[1]"] == 3 and cols["sus.score"] == 50.0
    assert Q.flatten([q], {})["sus.score"] == ""


AB = {"type": "ab_test", "title": "P", "order": "fixed", "ease_question": False, "preference": False,
      "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
      "tasks": [{"id": "t", "prompt": "Anything"}],
      "post_survey": [{"id": "sus", "preset": "sus"}],
      "final_survey": [{"id": "umux", "preset": "umux_lite"}]}


def test_ab_report_and_export(projects_dir, make_app):
    write_project(projects_dir, "ps", {"name": "PS", "locale": "en", "status": "live", "identity": "anonymous",
                                       "access": "open"}, {"ab": AB}, {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"})
    app = make_app(projects_dir, PS_ADMIN_PASSCODE="adm")
    for _ in range(3):
        c = app.test_client()
        c.post("/ps/", data={"action": "start", "consent": "1"})
        c.get("/ps/m/ab/")
        c.post("/ps/m/ab/s/intro")
        for n, value in ((1, 3), (2, 5)):
            c.post(f"/ps/m/ab/s/brief{n}")
            cfg = extract_json(c.get(f"/ps/m/ab/s/tasks{n}").get_data(as_text=True), "TASKS")
            c.post(cfg["submitUrl"].replace("__T__", "t"), json={"answer": {"answer": "x"}, "metrics": {}})
            # A: all 3 -> 50. B: positive items 5, negative items 1 -> 100.
            form = {f"sus.{i}": str(value if i % 2 or value == 3 else 1) for i in range(1, 11)}
            assert c.post(f"/ps/m/ab/s/survey{n}", data=form).status_code == 302
        assert c.post("/ps/m/ab/s/final", data={"umux.1": "7", "umux.2": "4"}).status_code == 302
    admin = app.test_client()
    admin.post("/ps/admin/", data={"passcode": "adm"})
    html = admin.get("/ps/admin/m/ab/").get_data(as_text=True)
    assert "How easy people found it" in html
    assert "50.0" in html and "100.0" in html and "A+" in html
    assert "People rated New 50 points higher than Old." in html and "(3 pairs)" in html
    assert "75.0" in html and "SUS-equivalent 71.7" in html      # UMUX-Lite (6+3)/12*100
    text = admin.get("/ps/admin/m/ab/export.csv").get_data(as_text=True).lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert sorted({r["post.sus.score"] for r in rows}) == ["100.0", "50.0"]
    assert {r["final.umux.score"] for r in rows} == {"75.0"}


def test_studio_writes_presets_back():
    from testbench.studio import module_to_ui_data
    ui = module_to_ui_data("ab", None, AB)
    assert ui["post"][0]["preset"] == "sus" and ui["final"][0]["preset"] == "umux_lite"
