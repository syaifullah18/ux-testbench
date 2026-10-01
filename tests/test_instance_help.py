from testbench import moderation


def superadmin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["tb_admin"] = ["*"]
    return c


def test_help_page_render(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = app.test_client()

    # /help route
    r = c.get("/help")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    assert "Choosing a module" in html
    assert "Draft, live and closed" in html
    assert "Reading results" in html
    assert "Launch checklist" in html
    assert "Indicative" in html
    assert "Intervals" in html
    assert "A/B verdict" in html
    assert 'aria-current="page">Help</a>' in html

    # /admin/help route
    r2 = c.get("/admin/help")
    assert r2.status_code == 200

    # trailing slashes
    assert c.get("/help/").status_code == 200
    assert c.get("/admin/help/").status_code == 200


def test_instance_page_access_control(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = app.test_client()

    # Unauthenticated user visiting instance
    r = c.get("/instance")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "You do not have access to this page" in html
    assert "Only the superadmin can open the instance." in html


def test_instance_page_superadmin_views(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # 1. Overview view
    r = c.get("/instance")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "At a glance" in html
    assert "Configuration" in html
    assert "Limits in force" in html
    assert "Recent operator actions" in html
    assert 'aria-current="page">Instance</a>' in html

    # 2. Studies view
    r_studies = c.get("/instance?s=studies")
    assert r_studies.status_code == 200
    html_s = r_studies.get_data(as_text=True)
    assert "Studies" in html_s
    assert "/example" in html_s
    assert "Take offline" in html_s

    # 3. Audit log view
    r_audit = c.get("/instance?s=audit")
    assert r_audit.status_code == 200
    html_a = r_audit.get_data(as_text=True)
    assert "Audit log" in html_a
    assert "Every operator action, newest first." in html_a


def test_instance_actions_take_offline_and_restore(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Initially not offline
    with app.app_context():
        assert not moderation.is_offline("example")

    # Take offline
    r = c.post(
        "/admin/instance/action",
        json={"action": "offline", "slug": "example", "reason": "Phishing report"},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    with app.app_context():
        assert moderation.is_offline("example")

    # Studies view shows Offline badge and Restore button
    r_studies = c.get("/instance?s=studies")
    html_s = r_studies.get_data(as_text=True)
    assert "Offline" in html_s
    assert "Restore" in html_s

    # Restore study
    r_res = c.post(
        "/admin/instance/action",
        json={"action": "restore", "slug": "example", "reason": "False alarm"},
    )
    assert r_res.status_code == 200
    assert r_res.get_json()["ok"] is True

    with app.app_context():
        assert not moderation.is_offline("example")


def test_instance_public_mode_platform_admin(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    from testbench import users
    with app.app_context():
        uid, _ = users.create_user("admin@platform.org", "Platform Admin", "Password123!")
        users.update_user(uid, is_platform_admin=1)

    c = app.test_client()
    # Log in
    c.post("/login", data={"email": "admin@platform.org", "password": "Password123!"})

    # Visiting /admin/instance must not crash with AttributeError
    r = c.get("/admin/instance")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "At a glance" in html

    # Performing action via /admin/instance/action must not crash with AttributeError
    r_act = c.post("/admin/instance/action", json={"action": "offline", "slug": "example", "reason": "Test"})
    assert r_act.status_code == 200
    assert r_act.get_json()["ok"] is True

