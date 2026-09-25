"""Phase 4: production hardening — headers, error pages, health, rate limits, local assets."""
from pathlib import Path

from testbench import rate_limit


def test_security_headers_and_request_id(projects_dir, make_app):
    app = make_app(projects_dir)
    resp = app.test_client().get("/s/example/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert resp.headers["X-Request-ID"]
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'self'" in csp


def test_request_id_from_the_proxy_is_echoed(projects_dir, make_app):
    app = make_app(projects_dir)
    resp = app.test_client().get("/s/example/", headers={"X-Request-ID": "abc123"})
    assert resp.headers["X-Request-ID"] == "abc123"


def test_csp_drops_cdn_hosts_when_assets_are_local(projects_dir, make_app):
    app = make_app(projects_dir, LOCAL_ASSETS="1")
    csp = app.test_client().get("/s/example/").headers["Content-Security-Policy"]
    assert "cdn.tailwindcss.com" not in csp
    assert "fonts.googleapis.com" not in csp


def test_local_assets_replace_cdn_tags(projects_dir, make_app):
    # The CDN build first: make_app sets environment variables, and they stay set for the rest of
    # the test, so the default case has to be checked before LOCAL_ASSETS is turned on.
    cdn_app = make_app(projects_dir)
    assert "cdn.tailwindcss.com" in cdn_app.test_client().get("/s/example/").get_data(as_text=True)

    app = make_app(projects_dir, LOCAL_ASSETS="1")
    body = app.test_client().get("/s/example/").get_data(as_text=True)
    assert "cdn.tailwindcss.com" not in body
    assert "static/vendor/tailwind.css" in body


def test_error_pages_are_friendly_and_carry_a_reference(projects_dir, make_app):
    app = make_app(projects_dir)
    resp = app.test_client().get("/no-such-study/")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert "Page not found" in body
    assert "Reference:" in body
    assert "Traceback" not in body


def test_health_reports_details_and_fails_loudly(projects_dir, make_app):
    app = make_app(projects_dir)
    payload = app.test_client().get("/health").get_json()
    assert payload["ok"] is True
    assert payload["config_ok"] is True
    assert payload["data_dir_writable"] is True
    assert payload["disk_free_mb"] > 0
    assert payload["projects"] >= 1


def test_health_turns_unhealthy_when_a_project_yaml_breaks(projects_dir, make_app):
    app = make_app(projects_dir)
    client = app.test_client()
    assert client.get("/health").get_json()["ok"] is True

    (projects_dir / "example" / "project.yaml").write_text("name: [broken", encoding="utf-8")
    with app.app_context():
        app.extensions["testbench"].refresh(force=True)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.get_json()["config_ok"] is False


def test_rate_limit_survives_in_the_database_and_is_shared(projects_dir, make_app):
    """Two clients of the same app are two requests; the count must be the same for both, which
    is what makes the limit hold across gunicorn workers."""
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    with app.app_context():
        rate_limit.clear("10.0.0.1", "auth_login")
        for _ in range(3):
            rate_limit.record_failed_login("10.0.0.1", "auth_login")
        assert rate_limit.attempts("10.0.0.1", "auth_login") == 3
        assert rate_limit.is_rate_limited("10.0.0.1", "auth_login", max_attempts=3)
        assert not rate_limit.is_rate_limited("10.0.0.2", "auth_login", max_attempts=3)
        rate_limit.clear("10.0.0.1", "auth_login")
        assert rate_limit.attempts("10.0.0.1", "auth_login") == 0


def test_rate_limit_falls_back_to_memory_without_an_app(projects_dir, make_app):
    rate_limit.clear("10.0.0.3", "signup")
    rate_limit.record_failed_login("10.0.0.3", "signup")
    assert rate_limit.attempts("10.0.0.3", "signup") == 1
    rate_limit.clear("10.0.0.3", "signup")


def test_sqlite_runs_in_wal_mode(projects_dir, make_app):
    from testbench import storage, users
    app = make_app(projects_dir)
    with app.test_request_context("/"):
        assert storage.connect("example").execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert users.system_db().execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_public_mode_marks_the_session_cookie_secure(projects_dir, make_app):
    public = make_app(projects_dir, TESTBENCH_MODE="public")
    # TESTING is on in the fixture, which is what keeps the test client usable over http.
    assert public.config["TESTBENCH_MODE"] == "public"
    from testbench import create_app
    live = create_app({"TESTBENCH_MODE": "public", "SECRET_KEY": "x",
                       "PROJECTS_DIRS": [str(projects_dir)]})
    assert live.config["SESSION_COOKIE_SECURE"] is True


def test_packaging_files_are_present_and_consistent():
    root = Path(__file__).resolve().parent.parent
    for name in ("Dockerfile", "docker-compose.yml", "Caddyfile",
                 "scripts/backup.sh", "scripts/build-assets.sh", "tailwind.config.js"):
        assert (root / name).is_file(), name
    compose = (root / "docker-compose.yml").read_text()
    assert "USERCONTENT_DOMAIN" in compose   # the two-origin setup from the plan
    caddy = (root / "Caddyfile").read_text()
    assert "frame-ancestors" in caddy


def test_every_page_shares_one_brand_palette(projects_dir, make_app):
    """The landing page once carried its own hardcoded indigo scale while the rest of the app
    used the project's colour, so the two never matched and brand-300/400/950 resolved to
    nothing at all. Both now come from the same custom properties."""
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    client = app.test_client()

    for path in ("/", "/s/example/", "/login", "/about"):
        body = client.get(path).get_data(as_text=True)
        for step in (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950):
            assert f"--brand-{step}:" in body, f"{path} is missing --brand-{step}"

    # A project's own primary colour reaches its participant pages.
    example = client.get("/s/example/").get_data(as_text=True)
    assert "--brand-500: 15 118 110" in example      # #0F766E from projects/example
    # The landing page has no project, so it falls back to the default blue.
    assert "--brand-500: 37 99 235" in client.get("/").get_data(as_text=True)


def test_the_landing_page_honours_local_assets(projects_dir, make_app):
    """It used to hardcode its own CDN tags, so LOCAL_ASSETS silently did not apply to the most
    visited public page and participant IPs still reached a CDN."""
    cdn = make_app(projects_dir, TESTBENCH_MODE="public")
    assert "cdn.tailwindcss.com" in cdn.test_client().get("/").get_data(as_text=True)

    local = make_app(projects_dir, TESTBENCH_MODE="public", LOCAL_ASSETS="1")
    body = local.test_client().get("/").get_data(as_text=True)
    for host in ("cdn.tailwindcss.com", "fonts.googleapis.com", "cdnjs.cloudflare.com",
                 "fonts.gstatic.com"):
        assert host not in body, f"landing page still contacts {host}"
    assert body.count("static/vendor/") == 3


def test_the_two_tailwind_configs_define_the_same_tokens():
    """A class present in one build and absent from the other loses its colour silently."""
    root = Path(__file__).resolve().parent.parent
    head = (root / "testbench" / "templates" / "_head.html").read_text()
    config = (root / "tailwind.config.js").read_text()
    for token in ("--brand-500", "--nav-rgb"):
        assert token in head and token in config, token
    for name in ("brand", "nav", "height"):
        assert name in head and name in config, name
