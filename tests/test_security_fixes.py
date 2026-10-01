import pytest
from pathlib import Path
from testbench import users, mail
from testbench.context import Ctx
from tests.conftest import write_project


def test_first_click_action_does_not_leak_project_files(projects_dir, make_app):
    # 1. Setup project with a first_click module
    write_project(
        projects_dir,
        "fc-leak-test",
        {"name": "FC Test", "access": "open"},
        {
            "fc1": {
                "type": "first_click",
                "title": "First Click",
                "tasks": [
                    {"id": "t1", "prompt": "Click logo", "image": "prototypes/logo.png"}
                ],
            }
        },
        files={
            "prototypes/logo.png": "PNG-BYTES",
            "secret.txt": "TOP-SECRET",
            ".history/project.yaml.bak": "BACKUP-SECRET",
        },
    )

    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["tb_admin"] = ["*"]

    # Allowed configured image should succeed via admin action
    res_img = client.get("/fc-leak-test/admin/m/fc1/x/prototypes/logo.png")
    assert res_img.status_code == 200
    assert res_img.get_data() == b"PNG-BYTES"

    # Non-configured files must be refused (404)
    assert client.get("/fc-leak-test/admin/m/fc1/x/project.yaml").status_code == 404
    assert client.get("/fc-leak-test/admin/m/fc1/x/secret.txt").status_code == 404
    assert client.get("/fc-leak-test/admin/m/fc1/x/.history/project.yaml.bak").status_code == 404
    assert client.get("/fc-leak-test/admin/m/fc1/x/../secret.txt").status_code == 404


def test_login_open_redirect_protection(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    with app.app_context():
        uid, problems = users.create_user("user@example.org", "User", "a-long-password")
        assert not problems, problems
        users.set_email_verified(uid)

    client = app.test_client()

    # External URL in next query param must be rejected in POST /login
    resp = client.post(
        "/login?next=https://evil.com",
        data={"email": "user@example.org", "password": "a-long-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/"

    # Scheme-relative URL must be rejected
    resp = client.post(
        "/login?next=//evil.com/path",
        data={"email": "user@example.org", "password": "a-long-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/"

    # Backslash trick must be rejected
    resp = client.post(
        r"/login?next=/\evil.com",
        data={"email": "user@example.org", "password": "a-long-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/"

    # Valid internal relative path is allowed
    resp = client.post(
        "/login?next=/app/p/study/",
        data={"email": "user@example.org", "password": "a-long-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/app/p/study/"

    # Already logged in with next=https://evil.com -> redirected to /app/
    resp_logged = client.get("/login?next=https://evil.com")
    assert resp_logged.status_code == 302
    assert resp_logged.headers["Location"] == "/app/"


def test_forgot_password_rate_limiting_and_host_header_poisoning(projects_dir, make_app, monkeypatch):
    app = make_app(
        projects_dir,
        TESTBENCH_MODE="public",
        APP_DOMAIN="testbench.example.org",
        SESSION_COOKIE_SECURE="1",
    )
    with app.app_context():
        uid, problems = users.create_user("target@example.org", "Target", "a-long-password")
        assert not problems, problems

    client = app.test_client()

    captured_resets = []

    def mock_send_password_reset(email, token, base_url):
        captured_resets.append({"email": email, "token": token, "base_url": base_url})

    monkeypatch.setattr(mail, "send_password_reset", mock_send_password_reset)

    # 1. Host header poisoning protection: Host header is evil.com, but reset link uses APP_DOMAIN
    resp = client.post(
        "/forgot",
        data={"email": "target@example.org"},
        headers={"Host": "evil.com"},
    )
    assert resp.status_code == 200
    assert len(captured_resets) == 1
    assert captured_resets[0]["base_url"] == "https://testbench.example.org"
    assert "evil.com" not in captured_resets[0]["base_url"]

    # 2. Rate limiting: 4 more requests (total 5) should succeed
    for _ in range(4):
        client.post("/forgot", data={"email": "target@example.org"})
    assert len(captured_resets) == 5

    # 6th request must be rate-limited and NOT trigger a password reset email
    resp_limited = client.post("/forgot", data={"email": "target@example.org"})
    assert resp_limited.status_code == 200
    assert len(captured_resets) == 5  # No 6th reset email


def test_ab_launch_checklist_runs_and_reports_status(projects_dir, make_app):
    # 1. A/B test with prototype files so it boots cleanly, but tasks have no accept key
    write_project(
        projects_dir,
        "ab-checklist-test",
        {"name": "AB Checklist", "status": "draft", "access": "open"},
        {
            "ab1": {
                "type": "ab_test",
                "title": "AB Test",
                "variants": {
                    "A": {"label": "A", "file": "prototypes/a.html"},
                    "B": {"label": "B", "file": "prototypes/b.html"},
                },
                "tasks": [
                    {"id": "t1", "title": "T1", "prompt": "Prompt 1", "fields": [{"id": "f1"}]}
                ],
            }
        },
        files={
            "prototypes/a.html": "<!doctype html>A",
            "prototypes/b.html": "<!doctype html>B",
        },
    )

    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["tb_admin"] = ["*"]

    # At this point, prototypes exist, but task 1 has no accept key -> ab_keys shows warning
    resp = c.get("/ab-checklist-test/admin/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Both variant files are uploaded." in html
    assert "Task 1 has no answer key. You will grade it by hand." in html

    # 2. Now simulate missing prototype file
    pdir = projects_dir / "ab-checklist-test"
    (pdir / "prototypes" / "a.html").unlink()

    resp_missing = c.get("/ab-checklist-test/admin/")
    assert resp_missing.status_code == 200
    html_missing = resp_missing.get_data(as_text=True)

    assert "Missing file(s):" in html_missing
    assert "a.html" in html_missing

    # 3. Restore prototype file and add accept key
    (pdir / "prototypes" / "a.html").write_text("<!doctype html>A", encoding="utf-8")
    import yaml
    mod_yaml = pdir / "modules" / "ab1.yaml"
    data = yaml.safe_load(mod_yaml.read_text(encoding="utf-8"))
    data["tasks"][0]["accept"] = {"f1": ["correct"]}
    mod_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")

    resp_fixed = c.get("/ab-checklist-test/admin/")
    assert resp_fixed.status_code == 200
    html_fixed = resp_fixed.get_data(as_text=True)

    assert "Both variant files are uploaded." in html_fixed
    assert "All tasks have automated answer keys." in html_fixed
