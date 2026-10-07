"""Segments by identity pattern, and the carryover (order) check."""
import pytest

from testbench.config import ConfigError, load_all
from testbench.modules import MODULE_TYPES

from .conftest import extract_json, write_project

AB = {"type": "ab_test", "title": "S", "order": "code_parity", "ease_question": False, "preference": False,
      "variants": {"A": {"label": "Old", "file": "p/a.html"}, "B": {"label": "New", "file": "p/b.html"}},
      "tasks": [{"id": "t", "prompt": "Type 42", "fields": [{"id": "x", "label": "X"}], "accept": {"x": ["42"]}}],
      "segments": {"Verificator": "^VER-", "Approver": "^APR-"}}
FILES = {"p/a.html": "<p>A</p>", "p/b.html": "<p>B</p>"}


def run(app, code, times):
    """times: {variant: ms}. code_parity: odd codes see A first, even codes B first."""
    c = app.test_client()
    c.post("/sg/", data={"identity": code, "consent": "1"})
    c.get("/sg/m/ab/")
    c.post("/sg/m/ab/s/intro")
    for n in (1, 2):
        c.post(f"/sg/m/ab/s/brief{n}")
        html = c.get(f"/sg/m/ab/s/tasks{n}").get_data(as_text=True)
        cfg = extract_json(html, "TASKS")
        variant = "B" if "<p>B</p>" in c.get(cfg["frameUrl"]).get_data(as_text=True) else "A"
        r = c.post(cfg["submitUrl"].replace("__T__", "t"),
                   json={"answer": {"x": "42"}, "metrics": {"time_ms": times[variant]}})
        assert r.status_code == 200, r.get_json()
    c.get(r.get_json()["next"])


def app_with(projects_dir, make_app, conf=AB):
    write_project(projects_dir, "sg", {"name": "SG", "locale": "en", "status": "live", "access": "open",
                                       "identity": {"mode": "code", "pattern": "^[A-Z]{3}-\\d{2}$"}}, {"ab": conf}, FILES)
    app = make_app(projects_dir, SG_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/sg/admin/", data={"passcode": "adm"})
    return app, admin


def test_segments_table(projects_dir, make_app):
    app, admin = app_with(projects_dir, make_app)
    run(app, "VER-01", {"A": 10000, "B": 4000})
    run(app, "VER-02", {"A": 10000, "B": 6000})
    run(app, "APR-03", {"A": 5000, "B": 9000})
    run(app, "OPS-04", {"A": 5000, "B": 5000})
    html = admin.get("/sg/admin/m/ab/").get_data(as_text=True)
    assert "By group" in html and "read these as hints" in html
    for name, n, diff in (("Verificator", 2, "+5.0s"), ("Approver", 1, "-4.0s"), ("Other", 1, "+0.0s")):
        row = html.split(f"<td>{name}</td>", 1)[1].split("</tr>", 1)[0]
        assert f">{n}</td>" in row and diff in row


def test_carryover_warning_on_sign_flip(projects_dir, make_app):
    app, admin = app_with(projects_dir, make_app, dict(AB, segments={}))
    run(app, "VER-01", {"A": 10000, "B": 4000})   # A first: B faster
    run(app, "VER-03", {"A": 10000, "B": 5000})
    run(app, "VER-02", {"A": 5000, "B": 8000})    # B first: B slower
    run(app, "VER-04", {"A": 5000, "B": 9000})
    html = admin.get("/sg/admin/m/ab/").get_data(as_text=True)
    assert "Check the order" in html and "5.5s faster for people who saw Old first" in html and "3.5s slower for people who saw it first" in html
    assert 'id="sg-h"' not in html            # no segments configured


def test_no_carryover_when_consistent(projects_dir, make_app):
    app, admin = app_with(projects_dir, make_app)
    run(app, "VER-01", {"A": 10000, "B": 4000})
    run(app, "VER-02", {"A": 10000, "B": 6000})
    assert "Check the order" not in admin.get("/sg/admin/m/ab/").get_data(as_text=True)


def test_bad_pattern_is_reported(tmp_path):
    write_project(tmp_path, "sg", {"name": "SG"}, {"ab": dict(AB, segments={"Bad": "(unclosed"})}, FILES)
    with pytest.raises(ConfigError) as err:
        load_all(MODULE_TYPES, [tmp_path])
    assert "segments.Bad: not a valid regex" in "\n".join(err.value.problems)


def test_studio_keeps_segments():
    from testbench.studio import module_to_ui_data
    assert module_to_ui_data("ab", None, AB)["keepModule"] == {"segments": AB["segments"]}
