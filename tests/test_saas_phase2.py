import pytest
from testbench import users
from .conftest import write_project


def test_landing_page_modes(projects_dir, make_app):
    # 1. Internal mode: shows index.html with listed projects
    app_internal = make_app(projects_dir, TESTBENCH_MODE="internal")
    c_int = app_internal.test_client()
    resp_int = c_int.get("/")
    assert resp_int.status_code == 200
    assert "City Library" in resp_int.get_data(as_text=True)

    # 2. Public mode: shows modern marketing landing page
    app_pub = make_app(projects_dir, TESTBENCH_MODE="public")
    c_pub = app_pub.test_client()
    resp_pub = c_pub.get("/")
    assert resp_pub.status_code == 200
    body = resp_pub.get_data(as_text=True)
    assert "Evidence-driven UX research" in body
    assert "Create Free Account" in body
    assert "Try Demo Study" in body


def test_url_move_and_legacy_redirects(projects_dir, make_app):
    app_pub = make_app(projects_dir, TESTBENCH_MODE="public")
    c = app_pub.test_client()

    # Legacy participant URL /example/ should permanently redirect to /s/example/
    r = c.get("/example/")
    assert r.status_code == 301
    assert r.location.endswith("/s/example/")

    # Canonical participant URL /s/example/ should serve the page
    r2 = c.get("/s/example/")
    assert r2.status_code == 200

    # Module subpath legacy redirect
    r3 = c.get("/example/m/profile/")
    assert r3.status_code == 301
    assert r3.location.endswith("/s/example/m/profile/")


def test_project_lifecycle_status(projects_dir, make_app):
    # Setup closed project
    write_project(projects_dir, "closed-study",
                  {"name": "Old Study", "status": "closed"},
                  {"fb": {"type": "survey", "title": "Feedback", "pages": [{"id": "p1", "questions": [{"id": "q1", "type": "text", "prompt": "Hi"}]}]}})
    
    # Setup draft project
    write_project(projects_dir, "draft-study",
                  {"name": "Draft Study", "status": "draft"},
                  {"fb": {"type": "survey", "title": "Feedback", "pages": [{"id": "p1", "questions": [{"id": "q1", "type": "text", "prompt": "Hi"}]}]}})

    app = make_app(projects_dir, TESTBENCH_MODE="public")
    c = app.test_client()

    # Closed study renders closed page
    r_closed = c.get("/s/closed-study/")
    assert r_closed.status_code == 200
    assert "Study Completed" in r_closed.get_data(as_text=True)

    # Draft study renders draft preview notification for anonymous visitor
    r_draft = c.get("/s/draft-study/")
    assert r_draft.status_code == 200
    assert "Draft Mode" in r_draft.get_data(as_text=True)


def test_researcher_dashboard_and_creation(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    c = app.test_client()

    # Unauthenticated /app/ redirects to login
    r = c.get("/app/")
    assert r.status_code == 302
    assert "/login" in r.location

    # Create user and log in
    with app.app_context():
        uid, errs = users.create_user("researcher@test.org", "Test Researcher", "S3cureP@ssword2026!")
        assert not errs
        users.set_email_verified(uid)

    r_login = c.post("/login", data={"email": "researcher@test.org", "password": "S3cureP@ssword2026!"}, follow_redirects=True)
    assert r_login.status_code == 200

    # Authenticated /app/ shows dashboard
    r_dash = c.get("/app/")
    assert r_dash.status_code == 200
    assert "Researcher Dashboard" in r_dash.get_data(as_text=True)

    # Create new study
    r_create = c.post("/app/new", data={
        "name": "Checkout Usability Study",
        "slug": "checkout-study",
        "locale": "en",
        "source": "blank",
    }, follow_redirects=True)
    assert r_create.status_code == 200

    # User is owner and project appears in dashboard
    r_dash2 = c.get("/app/")
    dash_html = r_dash2.get_data(as_text=True)
    assert "Checkout Usability Study" in dash_html
    assert "checkout-study" in dash_html


def test_public_pages_and_docs(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    c = app.test_client()

    for path in ("/about", "/privacy", "/terms", "/report", "/explore", "/docs/configuration"):
        resp = c.get(path)
        assert resp.status_code == 200, f"Failed for {path}"
