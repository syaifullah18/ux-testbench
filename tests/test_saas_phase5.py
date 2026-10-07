"""Phase 5: launch controls — sign-up modes, invitations and team roles."""
from testbench import users


def make_user(app, email, verified=True, admin=False):
    with app.app_context():
        uid, problems = users.create_user(email, email.split("@")[0], "a-long-password")
        assert not problems, problems
        if verified:
            users.set_email_verified(uid)
        if admin:
            users.update_user(uid, is_platform_admin=1)
        return uid


def login(app, client, email):
    with app.app_context():
        user = users.get_user_by_email(email)
        sid = users.create_session(user["id"])
    with client.session_transaction() as sess:
        sess["tb_auth_sid"] = sid
    return user


def owner_with_project(app, slug="study", email="owner@example.org"):
    make_user(app, email)
    client = app.test_client()
    login(app, client, email)
    resp = client.post("/app/new", data={"slug": slug, "name": "Study", "source": "blank"})
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return client


# ---------------------------------------------------------------- sign-up modes

def test_open_signup_is_the_default(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    assert app.test_client().get("/signup").status_code == 200


def test_closed_signup_refuses_everyone(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public", SIGNUP_MODE="closed")
    client = app.test_client()
    assert "Sign-ups are closed" in client.get("/signup").get_data(as_text=True)
    client.post("/signup", data={"email": "x@example.org", "name": "X",
                                 "password": "a-long-password", "confirm": "a-long-password"})
    with app.app_context():
        assert users.get_user_by_email("x@example.org") is None


def test_invite_only_signup_needs_a_valid_invitation(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public", SIGNUP_MODE="invite")
    owner_with_project(app)

    stranger = app.test_client()
    assert "Invitation needed" in stranger.get("/signup").get_data(as_text=True)
    stranger.post("/signup", data={"email": "nope@example.org", "name": "N",
                                   "password": "a-long-password", "confirm": "a-long-password"})
    with app.app_context():
        assert users.get_user_by_email("nope@example.org") is None

    with app.app_context():
        owner = users.get_user_by_email("owner@example.org")
        _, token = users.create_invitation("study", "guest@example.org", "editor", owner["id"])

    invited = app.test_client()
    assert invited.get(f"/signup?invite={token}").status_code == 200
    resp = invited.post(f"/signup?invite={token}",
                        data={"email": "guest@example.org", "name": "Guest",
                              "password": "a-long-password", "confirm": "a-long-password"})
    assert resp.status_code == 200
    with app.app_context():
        guest = users.get_user_by_email("guest@example.org")
        assert guest is not None
        # The invitation proves the address, so no second verification email is needed, and the
        # membership is granted at sign-up rather than needing a second visit to the link.
        assert guest["email_verified_at"] is not None
        assert users.get_membership("study", guest["id"])["role"] == "editor"


# ---------------------------------------------------------------- invitations

def test_inviting_a_teammate_grants_the_role_on_acceptance(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner = owner_with_project(app)
    make_user(app, "mate@example.org")

    resp = owner.post("/app/p/study/members",
                      data={"action": "invite", "email": "mate@example.org", "role": "viewer"},
                      follow_redirects=True)
    # No SMTP in tests, so the owner gets the link to pass on by hand.
    body = resp.get_data(as_text=True)
    assert "Invitation ready" in body and "/invite/" in body
    with app.app_context():
        pending = users.pending_invitations("study")
        assert len(pending) == 1
        mate = users.get_user_by_email("mate@example.org")
        assert users.get_membership("study", mate["id"]) is None
        _, token = users.create_invitation("study", "mate@example.org", "viewer",
                                           users.get_user_by_email("owner@example.org")["id"])

    mate_client = app.test_client()
    login(app, mate_client, "mate@example.org")
    assert mate_client.get(f"/invite/{token}").status_code == 302
    with app.app_context():
        assert users.get_membership("study", mate["id"])["role"] == "viewer"

    # The study now shows up on their dashboard.
    assert "Study" in mate_client.get("/app/").get_data(as_text=True)


def test_an_invitation_cannot_be_reused_or_taken_by_another_address(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner_with_project(app)
    make_user(app, "mate@example.org")
    make_user(app, "other@example.org")
    with app.app_context():
        owner_id = users.get_user_by_email("owner@example.org")["id"]
        _, token = users.create_invitation("study", "mate@example.org", "editor", owner_id)

    # Signed in as the wrong person: refused, and no membership is created.
    wrong = app.test_client()
    login(app, wrong, "other@example.org")
    resp = wrong.get(f"/invite/{token}")
    assert resp.status_code == 403
    assert "for another address" in resp.get_data(as_text=True)
    with app.app_context():
        other = users.get_user_by_email("other@example.org")
        assert users.get_membership("study", other["id"]) is None

    mate = app.test_client()
    login(app, mate, "mate@example.org")
    assert mate.get(f"/invite/{token}").status_code == 302
    # Second use of the same link is refused.
    assert mate.get(f"/invite/{token}").status_code == 410


def test_a_revoked_invitation_stops_working(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner = owner_with_project(app)
    make_user(app, "mate@example.org")
    with app.app_context():
        owner_id = users.get_user_by_email("owner@example.org")["id"]
        inv_id, token = users.create_invitation("study", "mate@example.org", "editor", owner_id)

    owner.post("/app/p/study/members", data={"action": "revoke_invite", "invitation_id": inv_id})
    mate = app.test_client()
    login(app, mate, "mate@example.org")
    assert mate.get(f"/invite/{token}").status_code == 410


# ---------------------------------------------------------------- roles

def test_only_an_owner_manages_the_team(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner_with_project(app)
    make_user(app, "editor@example.org")
    with app.app_context():
        editor = users.get_user_by_email("editor@example.org")
        users.add_membership("study", editor["id"], "editor")

    editor_client = app.test_client()
    login(app, editor_client, "editor@example.org")
    assert editor_client.get("/app/p/study/members").status_code == 403
    assert editor_client.post("/app/p/study/members",
                              data={"action": "invite", "email": "x@example.org",
                                    "role": "owner"}).status_code == 403

    stranger = app.test_client()
    make_user(app, "stranger@example.org")
    login(app, stranger, "stranger@example.org")
    assert stranger.get("/app/p/study/members").status_code == 403


def test_the_last_owner_cannot_be_removed(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner = owner_with_project(app)
    with app.app_context():
        owner_id = users.get_user_by_email("owner@example.org")["id"]

    resp = owner.post("/app/p/study/members",
                      data={"action": "remove", "user_id": owner_id}, follow_redirects=True)
    assert "only owner" in resp.get_data(as_text=True)
    with app.app_context():
        assert users.get_membership("study", owner_id)["role"] == "owner"


def test_a_role_change_takes_effect(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner = owner_with_project(app)
    make_user(app, "mate@example.org")
    with app.app_context():
        mate = users.get_user_by_email("mate@example.org")
        users.add_membership("study", mate["id"], "viewer")
        assert users.can(mate, "study", "view") is True
        assert users.can(mate, "study", "edit") is False

    owner.post("/app/p/study/members",
               data={"action": "set_role", "user_id": mate["id"], "role": "editor"})
    with app.app_context():
        mate = users.get_user_by_email("mate@example.org")
        assert users.can(mate, "study", "edit") is True
        assert users.can(mate, "study", "manage") is False   # still not an owner


def test_invitation_routes_are_absent_in_internal_mode(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="internal")
    client = app.test_client()
    assert client.get("/invite/whatever").status_code == 404
    assert client.get("/app/p/study/members").status_code == 404
    # /signup has no route in internal mode, so it falls through to the participant catch-all and
    # 404s as a study that does not exist. "signup" is a reserved slug, so no study can claim it.
    assert client.get("/signup", follow_redirects=True).status_code == 404
    from testbench.config import RESERVED_SLUGS
    assert {"signup", "login", "app", "s", "invite"} <= RESERVED_SLUGS


def test_public_mode_example_project_not_admin_for_unauthenticated(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    client = app.test_client()

    # Unauthenticated visitor hitting example admin dashboard gets redirected to /login
    resp = client.get("/example/admin/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    # Write routes return 302 (redirect to login) or 403, and do not execute
    resp = client.post("/example/admin/status", data={"status": "closed"})
    assert resp.status_code in (302, 403)

    resp = client.post("/example/admin/p/1/delete")
    assert resp.status_code in (302, 403)

    resp = client.post("/example/admin/launch/passcode", json={"action": "enable", "passcode": "secret"})
    assert resp.status_code in (302, 401, 403)

    resp = client.post("/example/admin/studio/passcodes", data={"action": "save"})
    assert resp.status_code in (302, 403)


def test_viewer_cannot_perform_write_actions(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner_with_project(app, slug="study", email="owner@example.org")
    make_user(app, "viewer@example.org")

    with app.app_context():
        viewer = users.get_user_by_email("viewer@example.org")
        users.add_membership("study", viewer["id"], "viewer")
        assert users.can(viewer, "study", "view") is True
        assert users.can(viewer, "study", "edit") is False

        # Add a participant to test participant routes
        from testbench.web import get_project
        from testbench.context import Ctx
        proj = get_project("study")
        ctx = Ctx(proj, admin=True)
        ctx.conn.execute("INSERT INTO participants (identity, created_at, last_seen_at) VALUES ('tester-1', '2026-04-18T10:00:00Z', '2026-04-18T10:30:00Z')")
        ctx.conn.commit()

    client = app.test_client()
    login(app, client, "viewer@example.org")

    # Viewer can view dashboard and participants
    assert client.get("/study/admin/").status_code == 200
    assert client.get("/study/admin/participants").status_code == 200

    # But viewer is forbidden from write actions
    assert client.post("/study/admin/status", data={"status": "closed"}).status_code == 403
    assert client.post("/study/admin/launch/passcode", json={"action": "enable", "passcode": "secret"}).status_code == 403
    assert client.post("/study/admin/studio/passcodes", data={"participant_passcode": "123"}).status_code == 403
    assert client.post("/study/admin/p/1/delete").status_code == 403
    assert client.post("/study/admin/p/1", data={"grade": "pass"}).status_code == 403


def test_study_cloning_requires_view_permission_and_no_silent_fallback(projects_dir, make_app):
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    owner_with_project(app, slug="secret-study", email="owner@example.org")
    make_user(app, "other@example.org")

    client = app.test_client()
    login(app, client, "other@example.org")

    # Attempting to copy a study without view permission is denied
    resp_get = client.get("/app/new?from=secret-study", follow_redirects=True)
    assert "You do not have permission to copy" in resp_get.get_data(as_text=True)

    resp_post = client.post("/app/new", data={"slug": "stolen-study", "name": "Stolen", "source": "duplicate", "from": "secret-study"}, follow_redirects=True)
    assert "You do not have permission to copy" in resp_post.get_data(as_text=True)
    with app.app_context():
        from testbench.web import registry
        assert "stolen-study" not in registry().projects

    # Attempting to copy a non-existent study does not fall back to copying registry's first project
    resp_nonexistent = client.post("/app/new", data={"slug": "fallback-test", "name": "Fallback", "source": "duplicate", "from": "nonexistent-slug"}, follow_redirects=True)
    html_nonexistent = resp_nonexistent.get_data(as_text=True)
    assert "Source project" in html_nonexistent and "nonexistent-slug" in html_nonexistent and "not found" in html_nonexistent
    with app.app_context():
        from testbench.web import registry
        assert "fallback-test" not in registry().projects




def test_operator_sidebar_lists_accounts_and_reports_inside_the_shell(projects_dir, make_app):
    """Users and Reports used to open the overview shell with an empty body."""
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, "root@example.org", admin=True)
    make_user(app, "someone@example.org", verified=False)
    client = app.test_client()
    login(app, client, "root@example.org")
    page = client.get("/admin/instance/?s=users").get_data(as_text=True)
    assert 'aria-label="Operator pages"' in page          # same shell as Overview and Studies
    assert "someone@example.org" in page and "Unverified" in page
    assert "Make admin" in page
    reports = client.get("/admin/instance/?s=reports")
    assert reports.status_code == 200 and "Filed from /report" in reports.get_data(as_text=True)


def test_new_study_cancel_goes_back_to_the_studies_list(projects_dir, make_app):
    """Cancel rendered href="" (the URL was set in another template block), so it reloaded the wizard."""
    app = make_app(projects_dir, TESTBENCH_MODE="public")
    make_user(app, "owner@example.org")
    client = app.test_client()
    login(app, client, "owner@example.org")
    page = client.get("/app/new").get_data(as_text=True)
    assert 'href="/app/" id="cancel"' in page
