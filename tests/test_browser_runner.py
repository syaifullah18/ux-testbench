"""The task runner (static/task-runner.js) in a real browser: goals, timer, pages, misclicks,
validation errors and journey steps. Skipped unless Playwright and its Chromium are installed
(pip install playwright && playwright install chromium)."""
import threading
import time

import pytest

from testbench import storage
from testbench.modules.ab_test import rework

from .conftest import write_project

sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

PAGE = """<!doctype html><html><body>
<p id="filler">Some text to misclick on.</p>
<a href="#detail">Detail</a> <a href="#list">Back to list</a>
<input name="npwp" id="npwp" placeholder="NPWP">
<button id="check" onclick="document.getElementById('npwp').setAttribute('aria-invalid','true')">Check NPWP</button>
<button id="save" data-testbench-goal="saved" data-testbench-label="Save draft">Simpan</button>
<button id="post" onclick="parent.postMessage({testbench:'goal', id:'posted'}, '*')">Kirim</button>
</body></html>"""

AB = {"type": "ab_test", "title": "E2E", "order": "fixed", "ease_question": False, "preference": False,
      "variants": {"A": {"label": "A", "file": "p/a.html"}, "B": {"label": "B", "file": "p/b.html"}},
      "tasks": [{"id": "save", "prompt": "Save the draft", "goals": {"success": ["saved"]}, "optimal_steps": 2},
                {"id": "post", "prompt": "Send it", "goals": {"success": ["posted"]}}]}

JOURNEY = {"type": "journey_test", "title": "J", "order": "fixed", "baseline": "cur",
           "variants": {"cur": {"steps": [{"id": "submit", "role": "vendor", "file": "cur/submit.html", "goal": "submit-done"},
                                          {"id": "verify", "role": "verificator", "file": "cur/verify.html", "goal": "verify-done"}]},
                        "new": {"steps": [{"id": "submit", "role": "vendor", "file": "new/submit.html", "goal": "submit-done"}]}}}
JOURNEY_FILES = {f"{d}/{s}.html": f"<h1 id=title>{d}-{s}</h1><button id=go data-testbench-goal='{s}-done'>Go</button>"
                 for d, steps in (("cur", ["submit", "verify"]), ("new", ["submit"])) for s in steps}


@pytest.fixture
def live(projects_dir, make_app):
    """Starts the app on a free port; yields (app, base_url, browser page)."""
    from werkzeug.serving import make_server
    servers = []

    def start(module, files):
        write_project(projects_dir, "e2e", {"name": "E2E", "locale": "en", "status": "live", "identity": "anonymous",
                                            "access": "open"}, {"m": module}, files)
        app = make_app(projects_dir)
        server = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return app, f"http://127.0.0.1:{server.server_port}"

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # Chromium not downloaded
            pytest.skip(f"no Chromium for Playwright: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        yield start, page, errors
        browser.close()
    for s in servers:
        s.shutdown()


def submit_form(page):
    with page.expect_navigation():
        page.evaluate("""() => { const f = [...document.querySelectorAll('form')].find(f => f.method.toLowerCase() === 'post');
                                 const b = f.querySelector('button[type=submit], button:not([type])'); b ? b.click() : f.submit(); }""")


def enter(page, base):
    page.goto(base + "/e2e/")
    if page.locator("input[name=consent]").count():
        page.check("input[name=consent]")
    submit_form(page)
    page.goto(base + "/e2e/m/m/")
    submit_form(page)      # intro


def start_task(page):
    page.wait_for_selector("#btn-start", state="visible")
    page.click("#btn-start")
    return page.frame_locator("#proto")


def results(app):
    with app.app_context():
        conn = storage.connect("e2e")
        rows = {(r["position"], r["task_id"]): dict(r) for r in conn.execute("SELECT * FROM task_results")}
        events = {}
        for by_pos in storage.bulk_task_events(conn, list({r["session_id"] for r in rows.values()})).values():
            for pos, by_task in by_pos.items():
                for tid, e in by_task.items():
                    events[(pos, tid)] = e
    return rows, events


def test_goals_pages_misclicks_and_errors(live):
    start, page, errors = live
    app, base = start(AB, {"p/a.html": PAGE, "p/b.html": PAGE.replace("Some text", "B text")})
    enter(page, base)
    for _ in (1, 2):
        submit_form(page)  # brief
        frame = start_task(page)
        frame.locator("#save").wait_for()
        frame.locator("#filler").click()
        frame.locator("text=Detail").click()
        frame.locator("text=Back to list").click()
        frame.locator("text=Detail").click()
        frame.locator("#check").click()
        time.sleep(0.2)
        frame.locator("#save").click()
        frame = start_task(page)
        frame.locator("#post").click()
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)
    assert errors == []
    rows, events = results(app)
    for pos in (1, 2):
        save, ev = rows[(pos, "save")], events[(pos, "save")]
        assert save["auto_pass"] == 1 and rows[(pos, "post")]["auto_pass"] == 1
        assert save["time_ms"] == next(e["t_ms"] for e in ev if e["kind"] == "goal")    # timer stopped at the goal
        rw = rework({"optimal_steps": 2}, ev)
        assert (rw["back"], rw["misclicks"], rw["errors"]) == (1, 1, 1) and rw["lostness"] > 0
        assert "button: Save draft" in storage.loads(save["click_path"], [])


def test_journey_steps_load_their_own_pages(live):
    start, page, errors = live
    app, base = start(JOURNEY, JOURNEY_FILES)
    enter(page, base)
    seen = []
    for count in (2, 1):
        submit_form(page)  # brief
        for _ in range(count):
            frame = start_task(page)
            frame.locator("#go").wait_for()
            seen.append(frame.locator("#title").inner_text())
            frame.locator("#go").click()
            time.sleep(0.5)
    assert errors == [] and seen == ["cur-submit", "cur-verify", "new-submit"]
    rows, _ = results(app)
    assert sorted(rows) == [(1, "submit"), (1, "verify"), (2, "submit")]
    assert all(r["auto_pass"] == 1 for r in rows.values())
