"""Prototype preflight: static checks on A/B variant HTML."""
from pathlib import Path

from testbench import preflight

from .conftest import write_project

BROKEN = """<!doctype html><html><head>
<link rel="stylesheet" href="missing.css">
<link rel="stylesheet" href="ok.css">
<script src="https://evil.example.com/x.js"></script>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="//fonts.googleapis.com/css2?family=Inter">
</head><body>
<a href="https://elsewhere.example.org/">Out</a>
<a href="#top">Top</a>
<button></button>
<button aria-label="Save">💾</button>
<div role="button" data-testbench-label="Tab Data Pajak">Pajak</div>
<input type="text" placeholder="Search">
<input type="submit">
<button>Next</button><button>Next</button>
<img src="data:image/png;base64,AAAA">
<button data-testbench-goal="saved">Save draft</button>
</body></html>"""


def findings(tmp_path, html, local_assets=False, targets=(), goals=(), extra=None):
    proto = tmp_path / "proj" / "p"
    proto.mkdir(parents=True)
    (proto / "a.html").write_text(html)
    (proto / "ok.css").write_text("")
    for name, text in (extra or {}).items():
        (proto / name).write_text(text)
    return preflight.check_variant(tmp_path / "proj", proto / "a.html", local_assets, targets, goals,
                                   exclude=[proto / "b.html"])


def messages(found, severity=None):
    return [f["message"] for f in found if severity in (None, f["severity"])]


def test_assets_requests_and_links(tmp_path):
    found = findings(tmp_path, BROKEN)
    errors = messages(found, "error")
    assert "Style 'missing.css' is not in the upload" in errors
    assert any("evil.example.com, which the app blocks" in m for m in errors)
    assert not any("ok.css" in m for m in messages(found))
    warnings = messages(found, "warning")
    assert any("cdn.tailwindcss.com; this breaks if LOCAL_ASSETS=1" in m for m in warnings)
    assert any("fonts.googleapis.com; this breaks" in m for m in warnings)
    assert any("leaves the prototype (https://elsewhere" in m for m in warnings)
    assert not any("#top" in m or "data:" in m for m in messages(found))
    f = next(f for f in found if "missing.css" in f["message"])
    assert f["file"] == "p/a.html" and 'href="missing.css"' in f["snippet"] and f["fix"]


def test_local_assets_turns_cdn_into_errors(tmp_path):
    found = findings(tmp_path, BROKEN, local_assets=True)
    assert any("cdn.tailwindcss.com, which the app blocks" in m for m in messages(found, "error"))


def test_labels_mirror_the_runner(tmp_path):
    found = findings(tmp_path, BROKEN)
    unlabeled = [f for f in found if "has no label" in f["message"]]
    assert len(unlabeled) == 2                   # the empty <button> and the bare submit input
    dup = [m for m in messages(found) if "is used by 2 controls" in m]
    assert dup == ["Label 'Next' is used by 2 controls on this page"]


def test_first_click_targets_and_goals(tmp_path):
    found = findings(tmp_path, BROKEN, targets=["Tab Data Pajak", "Save", "Search", "Bandingkan"],
                     goals=["saved", "approved", "posted"],
                     extra={"app.js": "parent.postMessage({testbench: 'goal', id: 'posted'}, '*')",
                            "b.html": "<button>Bandingkan</button>"})
    fc = [f for f in found if "First-click target" in f["message"]]
    assert [f["message"].split("'")[1] for f in fc] == ["Bandingkan"]   # other variant's page never counts
    assert fc[0]["severity"] == "warning"        # the page has scripts, which may add it
    goals = [m for m in messages(found) if m.startswith("Goal ")]
    assert goals == ["Goal 'approved' is not on any data-testbench-goal and not in the prototype's script"]


def test_static_page_missing_target_is_an_error(tmp_path):
    found = findings(tmp_path, "<button>Save</button>", targets=["Approve"])
    assert [f["severity"] for f in found] == ["error"]


def test_launch_shows_and_blocks(projects_dir, make_app):
    ab = {"type": "ab_test", "title": "AB", "minutes": 70,
          "variants": {"A": {"file": "p/a.html"}, "B": {"file": "p/b.html"}},
          "tasks": [{"id": "t", "prompt": "Do it", "goals": {"success": ["done"]}}]}
    studio = projects_dir.parent / "data" / "projects"
    pdir = write_project(studio, "pf", {"name": "PF", "locale": "en", "status": "draft", "identity": "anonymous",
                                        "access": "open"}, {"ab": ab},
                         {"p/a.html": "<link rel=stylesheet href=gone.css><button data-testbench-goal=done>Done</button>",
                          "p/b.html": "<button data-testbench-goal=done>Done</button>"})
    app = make_app(projects_dir, PF_ADMIN_PASSCODE="adm")
    admin = app.test_client()
    admin.post("/pf/admin/", data={"passcode": "adm"})
    html = admin.get("/pf/admin/launch").get_data(as_text=True)
    assert "Prototype pages work" in html and "1 problem to fix before going live, 1 thing worth checking." in html
    assert "Style &#39;gone.css&#39; is not in the upload" in html
    assert "70 minutes" in html
    assert "Task 1 has no answer key" not in html     # goals grade the task
    r = admin.post("/pf/admin/status", data={"status": "live"})
    assert r.status_code == 302
    assert "status: draft" in (pdir / "project.yaml").read_text()
    (pdir / "p" / "gone.css").write_text("")
    admin.post("/pf/admin/status", data={"status": "live"})
    assert "status: live" in (pdir / "project.yaml").read_text()
