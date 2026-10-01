"""Comprehensive test suite verifying all form submit functionality across the application."""
import io
import json
from pathlib import Path
import pytest
from testbench import users, auth, moderation, storage


def superadmin(app):
    c = app.test_client()
    c.post("/admin/", data={"passcode": "root"})
    return c


def create_user_and_login(app, email="researcher@test.org", password="ValidPassword123!", is_admin=False):
    with app.app_context():
        uid, errs = users.create_user(email, "Dr. Researcher", password)
        assert not errs
        users.set_email_verified(uid)
        if is_admin:
            users.update_user(uid, is_platform_admin=1)
        u = users.get_user(uid)
    c = app.test_client()
    c.post("/login", data={"email": email, "password": password})
    return c, u


def test_auth_submits(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    c = app.test_client()

    # 1. Signup submit
    r = c.post("/signup", data={"name": "Alice", "email": "alice@test.org", "password": "Password123!"})
    assert r.status_code == 200
    assert "Check your email" in r.get_data(as_text=True)
    with app.app_context():
        alice = users.get_user_by_email("alice@test.org")
        assert alice is not None
        users.set_email_verified(alice["id"])

    # 2. Login submit (valid)
    r = c.post("/login", data={"email": "alice@test.org", "password": "Password123!"})
    assert r.status_code == 302
    assert "/app/" in r.location

    # 3. Login submit (invalid password)
    c2 = app.test_client()
    r = c2.post("/login", data={"email": "alice@test.org", "password": "wrong"})
    assert r.status_code == 200
    assert "Incorrect email or password" in r.get_data(as_text=True)

    # 4. Forgot password submit
    r = c.post("/forgot", data={"email": "alice@test.org"})
    assert r.status_code == 200
    assert "Check your inbox" in r.get_data(as_text=True)
    with app.app_context():
        token = users.create_token(alice["id"], "reset", hours=1)

    # 5. Reset password submit
    r = c.post(f"/reset/{token}", data={"password": "NewPassword123!", "confirm": "NewPassword123!"})
    assert r.status_code == 200
    assert "Password reset" in r.get_data(as_text=True)
    with app.app_context():
        alice_updated = users.get_user_by_email("alice@test.org")
        assert users.verify_password(alice_updated, "NewPassword123!") is True

    # 6. Profile update submit
    client, u = create_user_and_login(app, "bob@test.org", "Pass123456!")
    r = client.post("/account", data={"action": "update_profile", "name": "Bob Updated"})
    assert r.status_code == 200
    with app.app_context():
        assert users.get_user(u["id"])["name"] == "Bob Updated"

    # 7. Logout submit
    r = client.post("/logout")
    assert r.status_code == 302


def test_public_and_platform_submits(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    c, u = create_user_and_login(app, "owner@test.org", "Pass123456!", is_admin=True)

    # 1. Public report submit
    r = c.post("/report", data={"slug": "example", "reason": "Suspicious request for passwords", "contact": "user@test.org"})
    assert r.status_code == 200
    assert "Report Received" in r.get_data(as_text=True)
    with app.app_context():
        open_reps = moderation.list_reports(status="open")
        assert len(open_reps) > 0
        rep_id = open_reps[0]["id"]

    # 2. Platform handle report submit
    r = c.post(f"/app/admin/reports/{rep_id}", data={"status": "actioned", "note": "Checked and benign."})
    assert r.status_code == 302
    with app.app_context():
        row = users.system_db().execute("SELECT * FROM reports WHERE id = ?", (rep_id,)).fetchone()
        assert row["status"] == "actioned"

    # 3. Create new study submit
    r = c.post("/app/new", data={"name": "Public UX Test", "slug": "public-ux", "source": "blank", "locale": "en"})
    assert r.status_code == 302
    assert "/public-ux/" in r.location

    # 4. Public dashboard status submit
    r = c.post("/app/p/public-ux/status", data={"status": "live"})
    assert r.status_code == 302

    # 5. Team members submit (invite, role, remove)
    r = c.post("/app/p/public-ux/members", data={"action": "invite", "email": "teammate@test.org", "role": "editor"})
    assert r.status_code == 302
    with app.app_context():
        invs = users.pending_invitations("public-ux")
        assert len(invs) > 0
        inv_id = invs[0]["id"]

        # Revoke invitation submit
        r = c.post("/app/p/public-ux/members", data={"action": "revoke_invite", "invitation_id": inv_id})
        assert r.status_code == 302
        assert len(users.pending_invitations("public-ux")) == 0

        # Add member directly and change role submit
        teammate_id, _ = users.create_user("mate@test.org", "Teammate", "Pass123456!")
        users.add_membership("public-ux", teammate_id, "viewer")

    r = c.post("/app/p/public-ux/members", data={"action": "set_role", "user_id": teammate_id, "role": "editor"})
    assert r.status_code == 302
    with app.app_context():
        assert users.get_membership("public-ux", teammate_id)["role"] == "editor"

    # Remove member submit
    r = c.post("/app/p/public-ux/members", data={"action": "remove", "user_id": teammate_id})
    assert r.status_code == 302
    with app.app_context():
        assert users.get_membership("public-ux", teammate_id) is None

    # 6. Platform project explore & offline submit
    r = c.post("/app/admin/projects/public-ux/explore", data={"approve": "1"})
    assert r.status_code == 302
    r = c.post("/app/admin/projects/public-ux/offline", data={"reason": "Audit review"})
    assert r.status_code == 302
    with app.app_context():
        assert moderation.is_offline("public-ux") is True

    # Put back online
    r = c.post("/app/admin/projects/public-ux/offline", data={"online": "1"})
    assert r.status_code == 302
    with app.app_context():
        assert moderation.is_offline("public-ux") is False

    # 7. Platform toggle admin & disable user submit
    r = c.post(f"/app/admin/users/{teammate_id}/admin", data={"grant": "1"})
    assert r.status_code == 302
    with app.app_context():
        assert users.get_user(teammate_id)["is_platform_admin"] == 1

    r = c.post(f"/app/admin/users/{teammate_id}/disable", data={"enable": "0"})
    assert r.status_code == 302
    with app.app_context():
        assert users.get_user(teammate_id)["disabled_at"] is not None


def test_admin_and_studio_submits(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Create new project via studio.new_project
    r = c.post("/admin/projects/new", data={
        "slug": "submit-study", "name": "Submit Verification Study",
        "source": "blank", "locale": "en", "identity": "code", "access": "open"
    })
    assert r.status_code == 302

    # 2. Add module submit
    r = c.post("/submit-study/admin/studio/modules/new", data={
        "id": "survey-one", "title": "First Survey", "type": "survey"
    })
    assert r.status_code == 302

    # 3. Duplicate module submit
    r = c.post("/submit-study/admin/studio/modules/survey-one/duplicate")
    assert r.status_code == 302

    # 4. Delete duplicated module submit
    r = c.post("/submit-study/admin/studio/modules/survey-one-copy/delete")
    assert r.status_code == 302

    # 5. Save & check module content via AJAX submit
    valid_module_yaml = "type: survey\ntitle: Updated Survey Title\npages:\n  - id: p1\n    questions:\n      - id: q1\n        type: text\n        prompt: What is your feedback?\n"
    r = c.post(
        "/submit-study/admin/studio/modules/survey-one",
        data={"action": "check", "content": valid_module_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = c.post(
        "/submit-study/admin/studio/modules/survey-one",
        data={"action": "save", "content": valid_module_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 6. Save & check study settings via AJAX submit
    current_yaml = (Path(app.config["DATA_DIR"]) / "projects" / "submit-study" / "project.yaml").read_text(encoding="utf-8")
    updated_yaml = current_yaml.replace("Submit Verification Study", "New Verified Study Name")
    r = c.post(
        "/submit-study/admin/studio/settings",
        data={"action": "check", "content": updated_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = c.post(
        "/submit-study/admin/studio/settings",
        data={"action": "save", "content": updated_yaml},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 7. Passcode submit on launch page (participant & admin passcodes)
    r = c.post(
        "/submit-study/admin/launch/passcode",
        data=json.dumps({"kind": "participant", "action": "set", "passcode": "part123"}),
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = c.post(
        "/submit-study/admin/launch/passcode",
        data=json.dumps({"kind": "participant", "action": "clear"}),
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 8. Status update via AJAX submit
    r = c.post(
        "/submit-study/admin/status",
        data={"status": "live"},
        headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 9. Prototype upload & delete submit
    file_content = io.BytesIO(b"<!DOCTYPE html><html><body><h1>Prototype Test</h1></body></html>")
    r = c.post(
        "/submit-study/admin/studio/files/upload",
        data={"files": (file_content, "proto.html")},
        content_type="multipart/form-data"
    )
    assert r.status_code == 302
    assert (Path(app.config["DATA_DIR"]) / "projects" / "submit-study" / "prototypes" / "proto.html").is_file()

    r = c.post(
        "/submit-study/admin/studio/files/delete",
        data={"path": "prototypes/proto.html"}
    )
    assert r.status_code == 302
    assert not (Path(app.config["DATA_DIR"]) / "projects" / "submit-study" / "prototypes" / "proto.html").is_file()

    # 10. Instance action submit (offline & restore)
    r = c.post(
        "/admin/instance/action",
        data=json.dumps({"action": "offline", "slug": "submit-study", "reason": "Maintenance"}),
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r = c.post(
        "/admin/instance/action",
        data=json.dumps({"action": "restore", "slug": "submit-study", "reason": "Ready"}),
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 11. Study participant simulation and delete
    p_client = app.test_client()
    r = p_client.post("/submit-study/", data={"identity": "P01", "consent": "1"})
    assert r.status_code == 302

    # Check participant in admin
    with app.app_context():
        with storage.connect("submit-study") as conn:
            row = conn.execute("SELECT id FROM participants WHERE identity = 'P01'").fetchone()
            pid = row["id"]

    # Delete participant submit
    r = c.post(f"/submit-study/admin/p/{pid}/delete")
    assert r.status_code == 302
    with app.app_context():
        with storage.connect("submit-study") as conn:
            assert conn.execute("SELECT id FROM participants WHERE id = ?", (pid,)).fetchone() is None

    # 12. Delete study submit
    r = c.post("/submit-study/admin/studio/delete", data={"confirm": "submit-study", "with_data": "1"})
    assert r.status_code == 302
    assert not (Path(app.config["DATA_DIR"]) / "projects" / "submit-study").exists()


def test_launch_passcodes_csrf_and_submits(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="internal")
    # Simulate production mode where app.testing is False to enforce CSRF validation
    app.testing = False
    c = app.test_client()

    # 1. Establish session and obtain CSRF token
    c.get("/admin/")
    with c.session_transaction() as sess:
        csrf_token = sess.get("csrf_token")
        sess["tb_admin"] = ["*"]

    # 2. POST without CSRF token must be rejected with 403
    r = c.post(
        "/example/admin/launch/passcode",
        data=json.dumps({"kind": "participant", "action": "set", "passcode": "part123"}),
        content_type="application/json"
    )
    assert r.status_code == 403

    # 3. POST with X-CSRFToken header succeeds
    r = c.post(
        "/example/admin/launch/passcode",
        data=json.dumps({"kind": "participant", "action": "set", "passcode": "part123"}),
        headers={"X-CSRFToken": csrf_token},
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 4. POST with csrf_token in JSON payload succeeds
    r = c.post(
        "/example/admin/launch/passcode",
        data=json.dumps({"kind": "participant", "action": "clear", "csrf_token": csrf_token}),
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 5. Admin passcode set with X-CSRFToken succeeds
    r = c.post(
        "/example/admin/launch/passcode",
        data=json.dumps({"kind": "admin", "action": "set", "passcode": "adm12345"}),
        headers={"X-CSRFToken": csrf_token},
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # 6. Admin passcode clear with X-CSRFToken succeeds
    r = c.post(
        "/example/admin/launch/passcode",
        data=json.dumps({"kind": "admin", "action": "clear"}),
        headers={"X-CSRFToken": csrf_token},
        content_type="application/json"
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

