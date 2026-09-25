"""Phase 3: quotas, moderation and privacy."""
from testbench import limits, moderation, users
from .conftest import write_project


def make_user(app, email="r@example.org", verified=True, admin=False):
    with app.app_context():
        uid, problems = users.create_user(email, "Researcher", "a-long-password")
        assert not problems, problems
        if verified:
            users.set_email_verified(uid)
        if admin:
            users.update_user(uid, is_platform_admin=1)
        return uid


def login(app, client, email="r@example.org"):
    with app.app_context():
        user = users.get_user_by_email(email)
        sid = users.create_session(user["id"])
    with client.session_transaction() as sess:
        sess["tb_auth_sid"] = sid
    return user


# ---------------------------------------------------------------- quotas

def test_limits_are_off_in_internal_mode(projects_dir, make_app, monkeypatch):
    app = make_app(projects_dir, TESTBENCH_MODE="internal")
    with app.app_context():
        assert limits.limit("projects_per_user") == 0
        assert limits.project_full("example") is False


def test_project_quota_blocks_creation(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public", LIMIT_PROJECTS_PER_USER="1")
    make_user(app)
    client = app.test_client()
    login(app, client)

    assert client.post("/app/new", data={"slug": "first", "name": "First", "source": "blank"}).status_code == 302
    resp = client.post("/app/new", data={"slug": "second", "name": "Second", "source": "blank"},
                       follow_redirects=True)
    assert "which is the limit for one account" in resp.get_data(as_text=True)
    with app.app_context():
        assert users.get_membership("second", users.get_user_by_email("r@example.org")["id"]) is None


def test_unverified_account_cannot_create_a_project(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, verified=False)
    client = app.test_client()
    login(app, client)
    resp = client.post("/app/new", data={"slug": "nope", "name": "Nope", "source": "blank"},
                       follow_redirects=True)
    assert "verify your email" in resp.get_data(as_text=True)


def test_signup_quota_per_ip(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public", LIMIT_SIGNUPS_PER_IP_PER_DAY="2")
    client = app.test_client()
    for i in range(2):
        resp = client.post("/signup", data={"email": f"u{i}@example.org", "name": "U",
                                            "password": "a-long-password", "confirm": "a-long-password"})
        assert "Check your" in resp.get_data(as_text=True) or resp.status_code == 200
    resp = client.post("/signup", data={"email": "u9@example.org", "name": "U",
                                        "password": "a-long-password", "confirm": "a-long-password"})
    assert "daily sign-up limit" in resp.get_data(as_text=True)
    with app.app_context():
        assert users.get_user_by_email("u9@example.org") is None


def test_participant_cap_turns_away_only_new_participants(tmp_path, make_app):
    base = tmp_path / "projects"
    base.mkdir()
    write_project(base, "capped", {"name": "Capped", "access": "open",
                                   "identity": {"mode": "code", "pattern": "^P\\d$"}},
                  {"fb": {"type": "survey", "title": "Feedback",
                          "pages": [{"id": "p1", "questions": [{"id": "q", "type": "text", "label": "Hi"}]}]}})
    app = make_app(base, TESTBENCH_MODE="public", LIMIT_PARTICIPANTS_PER_PROJECT="1")
    client = app.test_client()

    assert client.post("/s/capped/", data={"identity": "P1"}).status_code == 302
    client.get("/s/capped/logout")

    # A second, unknown participant is refused, with a page that explains why.
    resp = client.post("/s/capped/", data={"identity": "P2"})
    assert resp.status_code == 403
    assert "enough responses" in resp.get_data(as_text=True)

    # The one who already started can still sign back in.
    assert client.post("/s/capped/", data={"identity": "P1"}).status_code == 302


# ---------------------------------------------------------------- moderation

def test_report_is_stored_and_visible_to_platform_admin(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    client = app.test_client()
    resp = client.post("/report", data={"slug": "example", "reason": "Asks for my bank password",
                                        "contact": "who@example.org"})
    assert resp.status_code == 200
    with app.app_context():
        open_reports = moderation.list_reports("open")
        assert len(open_reports) == 1
        assert open_reports[0]["project_slug"] == "example"
        assert moderation.count_open_reports() == 1

    make_user(app, email="admin@example.org", admin=True)
    admin_client = app.test_client()
    login(app, admin_client, "admin@example.org")
    page = admin_client.get("/app/admin/reports").get_data(as_text=True)
    assert "bank password" in page


def test_taking_a_study_offline_hides_it_from_participants(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, email="admin@example.org", admin=True)
    client = app.test_client()
    login(app, client, "admin@example.org")

    assert client.get("/s/example/").status_code == 200
    resp = client.post("/app/admin/projects/example/offline", data={"reason": "phishing"})
    assert resp.status_code == 302

    participant = app.test_client()
    page = participant.get("/s/example/")
    assert page.status_code == 403
    assert "unavailable" in page.get_data(as_text=True)
    assert "phishing" not in page.get_data(as_text=True)   # the reason is internal

    with app.app_context():
        assert moderation.is_offline("example")
        assert any(e["action"] == "project.offline" for e in moderation.list_audit())

    client.post("/app/admin/projects/example/offline", data={"online": "1"})
    assert app.test_client().get("/s/example/").status_code == 200


def test_explore_lists_only_approved_studies(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, email="admin@example.org", admin=True)
    client = app.test_client()
    login(app, client, "admin@example.org")

    assert "City Library" not in app.test_client().get("/explore").get_data(as_text=True)
    client.post("/app/admin/projects/example/explore", data={"approve": "1"})
    assert "City Library" in app.test_client().get("/explore").get_data(as_text=True)


def test_platform_screens_refuse_non_admins(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app)
    anonymous = app.test_client()
    assert anonymous.get("/app/admin/").status_code == 302   # to the login page

    client = app.test_client()
    login(app, client)
    for path in ("/app/admin/", "/app/admin/users", "/app/admin/projects",
                 "/app/admin/reports", "/app/admin/audit"):
        assert client.get(path).status_code == 403, path


def test_disabling_an_account_revokes_its_sessions(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app)
    make_user(app, email="admin@example.org", admin=True)

    victim = app.test_client()
    user = login(app, victim)
    assert victim.get("/app/").status_code == 200

    admin_client = app.test_client()
    login(app, admin_client, "admin@example.org")
    admin_client.post(f"/app/admin/users/{user['id']}/disable", data={"enable": "0"})

    assert victim.get("/app/").status_code == 302   # signed out, back to login


def test_platform_admin_cannot_disable_themselves(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, email="admin@example.org", admin=True)
    client = app.test_client()
    user = login(app, client, "admin@example.org")
    resp = client.post(f"/app/admin/users/{user['id']}/disable", data={"enable": "0"},
                       follow_redirects=True)
    assert "cannot disable your own account" in resp.get_data(as_text=True)
    with app.app_context():
        assert users.get_user(user["id"])["disabled_at"] is None


# ---------------------------------------------------------------- privacy

def test_account_export_contains_the_account_and_not_other_accounts(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app)
    make_user(app, email="other@example.org")
    client = app.test_client()
    login(app, client)
    data = client.get("/account/export.json").get_json()
    assert data["account"]["email"] == "r@example.org"
    assert "other@example.org" not in str(data)


def test_deleting_an_account_requires_deciding_about_sole_owned_studies(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app)
    client = app.test_client()
    user = login(app, client)
    client.post("/app/new", data={"slug": "mine", "name": "Mine", "source": "blank"})

    refused = client.post("/account", data={"action": "delete_account",
                                            "confirm_email": "r@example.org"})
    assert "only owner" in refused.get_data(as_text=True)
    with app.app_context():
        assert users.get_user(user["id"]) is not None

    done = client.post("/account", data={"action": "delete_account",
                                         "confirm_email": "r@example.org",
                                         "projects_action": "delete"})
    assert done.status_code == 302
    with app.app_context():
        assert users.get_user(user["id"]) is None


def test_retention_clears_old_ip_addresses(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    with app.app_context():
        moderation.audit("test.action", actor="someone", ip="203.0.113.9")
        conn = users.system_db()
        conn.execute("UPDATE platform_audit SET at = '2000-01-01T00:00:00.000+00:00'")
        conn.commit()
        stats = moderation.prune_ips(days=30)
        assert stats["audit_ips_cleared"] == 1
        assert moderation.list_audit()[0]["ip"] is None
        assert moderation.list_audit()[0]["action"] == "test.action"   # the entry itself remains
