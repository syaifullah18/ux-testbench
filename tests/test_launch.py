def superadmin(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["tb_admin"] = ["*"]
    return c


def test_launch_page_render(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    r = c.get("/example/admin/launch")
    assert r.status_code == 200
    html = r.get_data(as_text=True)

    # Check key sections
    assert "Is this study ready?" in html
    assert "Access and passcodes" in html
    assert "Status" in html
    assert "Share" in html
    assert "Participant link" in html
    assert "Invitation text" in html
    assert "QR code" in html
    assert "Admin link" in html

    # Verify launch tab is marked active
    assert 'aria-current="page">Launch</a>' in html


def test_launch_page_app_route(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)
    r = c.get("/app/p/example/launch")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Is this study ready?" in html


def test_launch_passcode_flow(projects_dir, make_app):
    app = make_app(projects_dir, SUPERADMIN_PASSCODE="root")
    c = superadmin(app)

    # Try setting short passcode -> should fail
    r = c.post(
        "/example/admin/launch/passcode",
        json={"kind": "participant", "action": "set", "passcode": "123"},
    )
    assert r.status_code == 400
    assert r.get_json()["ok"] is False

    # Set valid participant passcode
    r = c.post(
        "/example/admin/launch/passcode",
        json={"kind": "participant", "action": "set", "passcode": "secret123"},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    # Verify launch page reflects set passcode
    r_page = c.get("/example/admin/launch")
    assert "Set here" in r_page.get_data(as_text=True)

    # Clear participant passcode
    r_clear = c.post(
        "/example/admin/launch/passcode",
        json={"kind": "participant", "action": "clear"},
    )
    assert r_clear.status_code == 200
    assert r_clear.get_json()["ok"] is True

    # Set admin passcode
    r_admin = c.post(
        "/example/admin/launch/passcode",
        json={"kind": "admin", "action": "set", "passcode": "adminsecret99"},
    )
    assert r_admin.status_code == 200
    assert r_admin.get_json()["ok"] is True
