"""Sign in with Google and GitHub. The provider is faked at the HTTP boundary (oauth._post_form and
oauth._get_json), so everything from the redirect to the session is the real code."""
from urllib.parse import parse_qs, urlsplit

import pytest

from testbench import oauth, users

GOOGLE = {"GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsecret"}
GITHUB = {"GITHUB_CLIENT_ID": "hid", "GITHUB_CLIENT_SECRET": "hsecret"}


@pytest.fixture
def provider(monkeypatch):
    """What the fake provider answers. Tests change these before finishing a sign-in."""
    answers = {
        "token": {"access_token": "tok"},
        "google": {"sub": "g-1", "email": "ana@example.org", "email_verified": True, "name": "Ana"},
        "github_user": {"id": 42, "login": "ana-gh", "name": "Ana"},
        "github_emails": [{"email": "ana@example.org", "primary": True, "verified": True}],
        "calls": [],
    }

    def post_form(url, data):
        answers["calls"].append(("POST", url, data))
        if isinstance(answers["token"], Exception):
            raise answers["token"]
        return answers["token"]

    def get_json(url, token):
        answers["calls"].append(("GET", url, token))
        if url.endswith("/user/emails"):
            return answers["github_emails"]
        if "github" in url:
            return answers["github_user"]
        return answers["google"]

    monkeypatch.setattr(oauth, "_post_form", post_form)
    monkeypatch.setattr(oauth, "_get_json", get_json)
    return answers


def public_app(make_app, projects_dir, **env):
    return make_app(projects_dir, TESTBENCH_MODE="public", **GOOGLE, **GITHUB, **env)


def start(client, name, **query):
    resp = client.get(f"/login/{name}", query_string=query)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return parse_qs(urlsplit(resp.headers["Location"]).query)["state"][0]


def sign_in(client, name="google", **query):
    state = start(client, name, **query)
    return client.get(f"/login/{name}/callback", query_string={"state": state, "code": "c"})


def signed_in(client):
    return client.get("/account").status_code == 200


def make_user(app, email, password="a-long-password", verified=True):
    with app.app_context():
        uid, problems = users.create_user(email, "Someone", password)
        assert not problems, problems
        if verified:
            users.set_email_verified(uid)
        return uid


# ---------------------------------------------------------------- the round trip

def test_buttons_appear_only_for_configured_providers(projects_dir, make_app, monkeypatch):
    app = make_app(projects_dir, TESTBENCH_MODE="public", **GOOGLE)
    html = app.test_client().get("/login").get_data(as_text=True)
    assert "Continue with Google" in html
    assert "GitHub" not in html
    assert app.test_client().get("/login/github").status_code == 404

    for key in GOOGLE:
        monkeypatch.delenv(key)
    bare = make_app(projects_dir, TESTBENCH_MODE="public")
    html = bare.test_client().get("/login").get_data(as_text=True)
    assert "Continue with" not in html and 'name="password"' in html


def test_redirect_carries_state_pkce_and_our_callback(projects_dir, make_app):
    app = public_app(make_app, projects_dir)
    resp = app.test_client().get("/login/google")
    url = urlsplit(resp.headers["Location"])
    q = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert q["client_id"] == ["gid"]
    assert q["redirect_uri"] == ["http://localhost/login/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"][0]
    assert len(q["state"][0]) > 30
    assert "gsecret" not in resp.headers["Location"]


def test_first_google_sign_in_creates_a_verified_account(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    resp = sign_in(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/app/")
    assert signed_in(client)
    with app.app_context():
        user = users.get_user_by_email("ana@example.org")
        assert user["name"] == "Ana" and user["email_verified_at"]
        assert not users.has_password(user)
        assert users.find_identity("google", "g-1")["id"] == user["id"]
    # The code went back with the PKCE verifier and our secret, server to server.
    _, _, sent = provider["calls"][0]
    assert sent["code"] == "c" and sent["code_verifier"] and sent["client_secret"] == "gsecret"


def test_second_sign_in_finds_the_same_account(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    sign_in(app.test_client())
    provider["google"]["email"] = "ana.new@example.org"   # the address may change; the id does not
    client = app.test_client()
    sign_in(client)
    assert signed_in(client)
    with app.app_context():
        assert len(users.list_users()) == 1


def test_github_uses_the_primary_verified_address(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    provider["github_emails"] = [
        {"email": "old@example.org", "primary": False, "verified": True},
        {"email": "Ana@Example.org", "primary": True, "verified": True},
    ]
    client = app.test_client()
    sign_in(client, "github")
    assert signed_in(client)
    with app.app_context():
        assert users.find_identity("github", "42")["email"] == "ana@example.org"


@pytest.mark.parametrize("name,patch", [
    ("google", {"google": {"sub": "g-1", "email": "ana@example.org", "email_verified": False}}),
    ("github", {"github_emails": [{"email": "ana@example.org", "primary": True, "verified": False}]}),
    ("github", {"github_emails": []}),
])
def test_an_unverified_address_is_refused(projects_dir, make_app, provider, name, patch):
    app = public_app(make_app, projects_dir)
    provider.update(patch)
    client = app.test_client()
    resp = sign_in(client, name)
    assert "No verified email" in resp.get_data(as_text=True)
    assert not signed_in(client)
    with app.app_context():
        assert users.list_users() == []


def test_state_must_match_and_is_single_use(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    assert client.get("/login/google/callback?code=c&state=x").status_code == 400

    state = start(client, "google")
    assert client.get("/login/google/callback", query_string={"state": "wrong", "code": "c"}).status_code == 400
    # The wrong attempt used the flow up; the real state is gone too.
    assert client.get("/login/google/callback", query_string={"state": state, "code": "c"}).status_code == 400

    state = start(client, "google")
    assert client.get("/login/google/callback", query_string={"state": state, "code": "c"}).status_code == 302
    assert client.get("/login/google/callback", query_string={"state": state, "code": "c"}).status_code == 400

    # A flow started for Google cannot be finished as GitHub.
    other = app.test_client()
    state = start(other, "google")
    assert other.get("/login/github/callback", query_string={"state": state, "code": "c"}).status_code == 400


def test_cancel_and_provider_failure_are_explained(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    state = start(client, "google")
    resp = client.get("/login/google/callback", query_string={"state": state, "error": "access_denied"})
    assert "Sign-in cancelled" in resp.get_data(as_text=True)

    provider["token"] = oauth.OAuthError("Could not reach oauth2.googleapis.com. Please try again.")
    resp = sign_in(client)
    assert resp.status_code == 502 and "Could not reach" in resp.get_data(as_text=True)
    assert not signed_in(client)


def test_next_is_kept_but_only_on_this_site(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    resp = sign_in(app.test_client(), next="/app/p/example/members")
    assert resp.headers["Location"].endswith("/app/p/example/members")
    resp = sign_in(app.test_client(), next="https://evil.example/")
    assert resp.headers["Location"].endswith("/app/")


# ---------------------------------------------------------------- existing accounts

def test_an_existing_verified_account_is_linked_and_keeps_its_password(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    uid = make_user(app, "ana@example.org")
    client = app.test_client()
    sign_in(client)
    assert signed_in(client)
    with app.app_context():
        assert users.find_identity("google", "g-1")["id"] == uid
        assert users.verify_password(users.get_user(uid), "a-long-password")
        assert len(users.list_users()) == 1


def test_an_unproven_password_account_loses_its_password_and_sessions(projects_dir, make_app, provider):
    """Someone registered the victim's address with a password and never verified it. When the
    real owner arrives through Google, the squatter's password and sessions must stop working."""
    app = public_app(make_app, projects_dir)
    uid = make_user(app, "ana@example.org", verified=False)
    with app.app_context():
        squatter_sid = users.create_session(uid)

    client = app.test_client()
    sign_in(client)
    assert signed_in(client)
    with app.app_context():
        user = users.get_user(uid)
        assert user["email_verified_at"]
        assert not users.verify_password(user, "a-long-password")
        assert users.load_session_user(squatter_sid) is None


def test_a_disabled_account_cannot_sign_in(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    uid = make_user(app, "ana@example.org")
    with app.app_context():
        users.update_user(uid, disabled_at="2026-01-01T00:00:00")
    client = app.test_client()
    assert sign_in(client).status_code == 403
    assert not signed_in(client)


# ---------------------------------------------------------------- sign-up modes and invitations

def test_closed_sign_ups_still_let_existing_people_in(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir, SIGNUP_MODE="closed")
    client = app.test_client()
    assert "Sign-ups are closed" in sign_in(client).get_data(as_text=True)
    with app.app_context():
        assert users.list_users() == []

    make_user(app, "ana@example.org")
    sign_in(client)
    assert signed_in(client)


def test_invite_mode_needs_an_invitation_for_that_address(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir, SIGNUP_MODE="invite")
    owner_id = make_user(app, "owner@example.org")
    with app.app_context():
        users.add_membership("example", owner_id, "owner")
        _, token = users.create_invitation("example", "ana@example.org", "editor", owner_id)

    stranger = app.test_client()
    assert "Invitation needed" in sign_in(stranger).get_data(as_text=True)

    client = app.test_client()
    assert client.get(f"/invite/{token}").status_code == 302   # holds the invitation, sends to sign-up
    sign_in(client)
    assert signed_in(client)
    with app.app_context():
        ana = users.get_user_by_email("ana@example.org")
        assert users.get_membership("example", ana["id"])["role"] == "editor"
        assert users.get_invitation(token) is None


def test_an_invitation_for_another_address_is_not_accepted(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    owner_id = make_user(app, "owner@example.org")
    with app.app_context():
        _, token = users.create_invitation("example", "bob@example.org", "editor", owner_id)
    client = app.test_client()
    client.get(f"/invite/{token}")
    sign_in(client)   # signs in as ana@example.org
    with app.app_context():
        ana = users.get_user_by_email("ana@example.org")
        assert users.get_membership("example", ana["id"]) is None
        assert users.get_invitation(token) is not None


def test_sign_up_cap_applies(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir, LIMIT_SIGNUPS_PER_IP_PER_DAY="1")
    sign_in(app.test_client())
    provider["google"] = {"sub": "g-2", "email": "bo@example.org", "email_verified": True, "name": "Bo"}
    resp = sign_in(app.test_client())
    assert "Too many sign-ups" in resp.get_data(as_text=True)


# ---------------------------------------------------------------- linking from /account

def test_linking_github_from_the_account_page(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    sign_in(client)   # Google
    provider["github_emails"] = [{"email": "ana.gh@example.org", "primary": True, "verified": True}]
    resp = sign_in(client, "github", link="1")
    assert resp.headers["Location"].endswith("/account")
    with app.app_context():
        ana = users.get_user_by_email("ana@example.org")
        assert {i["provider"] for i in users.list_identities(ana["id"])} == {"google", "github"}
    assert "Linked as ana.gh@example.org" in client.get("/account").get_data(as_text=True)


def test_a_provider_account_cannot_be_linked_to_two_people(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    sign_in(app.test_client(), "github")            # GitHub 42 now belongs to ana
    provider["google"] = {"sub": "g-2", "email": "bo@example.org", "email_verified": True, "name": "Bo"}
    bo = app.test_client()
    sign_in(bo)
    resp = sign_in(bo, "github", link="1")
    assert "already linked to another account" in resp.get_data(as_text=True)


def test_the_last_way_to_sign_in_cannot_be_unlinked(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    sign_in(client)
    resp = client.post("/account", data={"action": "unlink_identity", "provider": "google"})
    assert "only way you can sign in" in resp.get_data(as_text=True)
    with app.app_context():
        assert users.find_identity("google", "g-1") is not None

    # With a password as well, unlinking is fine.
    make_user(app, "pw@example.org")
    provider["google"] = {"sub": "g-9", "email": "pw@example.org", "email_verified": True}
    other = app.test_client()
    sign_in(other)
    other.post("/account", data={"action": "unlink_identity", "provider": "google"})
    with app.app_context():
        assert users.find_identity("google", "g-9") is None


def test_export_lists_linked_accounts(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    sign_in(client)
    data = client.get("/account/export.json").get_json()
    assert data["linked_accounts"][0]["provider"] == "google"


# ---------------------------------------------------------------- PASSWORD_LOGIN=off

def test_password_login_off_leaves_only_the_providers(projects_dir, make_app, provider):
    app = public_app(make_app, projects_dir, PASSWORD_LOGIN="off")
    make_user(app, "pw@example.org")
    client = app.test_client()

    html = client.get("/login").get_data(as_text=True)
    assert "Continue with Google" in html and 'name="password"' not in html
    assert 'name="password"' not in client.get("/signup").get_data(as_text=True)
    assert client.post("/login", data={"email": "pw@example.org", "password": "a-long-password"}).status_code == 404
    assert client.post("/signup", data={"email": "x@example.org", "name": "X", "password": "a-long-password"}).status_code == 404
    for path in ("/forgot", "/reset/abc", "/verify/abc"):
        assert client.get(path).status_code == 404, path

    sign_in(client)
    assert 'name="current_password"' not in client.get("/account").get_data(as_text=True)


def test_password_login_off_without_a_provider_refuses_to_start(projects_dir, make_app):
    with pytest.raises(RuntimeError, match="PASSWORD_LOGIN=off"):
        make_app(projects_dir, TESTBENCH_MODE="public", PASSWORD_LOGIN="off")


def test_create_admin_without_passwords(projects_dir, make_app, monkeypatch, tmp_path, provider):
    from testbench.__main__ import main
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("getpass.getpass", lambda *_: pytest.fail("asked for a password"))
    app = public_app(make_app, projects_dir, PASSWORD_LOGIN="off")
    assert main(["create-admin", "ana@example.org"]) == 0

    client = app.test_client()
    sign_in(client)
    with app.app_context():
        ana = users.get_user_by_email("ana@example.org")
        assert ana["is_platform_admin"] and not users.has_password(ana)
        assert users.find_identity("google", "g-1")["id"] == ana["id"]


# ---------------------------------------------------------------- invitation links

def test_the_owner_gets_an_invitation_link_without_smtp(projects_dir, make_app, provider, monkeypatch):
    monkeypatch.delenv("SMTP_URL", raising=False)
    app = public_app(make_app, projects_dir)
    owner = app.test_client()
    sign_in(owner)
    with app.app_context():
        users.add_membership("example", users.get_user_by_email("ana@example.org")["id"], "owner")

    sent = []
    monkeypatch.setattr("testbench.mail.send_invitation", lambda *a, **k: sent.append(a))
    resp = owner.post("/app/p/example/members", data={"action": "invite", "email": "bo@example.org",
                                                      "role": "viewer"}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert "Invitation ready" in html and "http://localhost/invite/" in html
    assert sent == []
    # Shown once: a reload does not show it again.
    assert "Invitation ready" not in owner.get("/app/p/example/members").get_data(as_text=True)

    monkeypatch.setenv("SMTP_URL", "smtp://mail.example.org:587")
    owner.post("/app/p/example/members", data={"action": "invite", "email": "cy@example.org", "role": "viewer"})
    assert len(sent) == 1


def test_a_link_to_a_provider_that_was_switched_off_still_shows(projects_dir, make_app, provider, monkeypatch):
    app = public_app(make_app, projects_dir)
    client = app.test_client()
    sign_in(client, "github")
    for key in GITHUB:
        monkeypatch.delenv(key)
    html = client.get("/account").get_data(as_text=True)
    assert "Linked as ana@example.org" in html and "Unlink" in html


def test_an_existing_member_accepting_an_invitation_through_google(projects_dir, make_app, provider):
    """/invite sends someone with an account to /login?next=/invite/<token>. The callback accepts
    the invitation, so it must not then send them to the link it has just used up."""
    app = public_app(make_app, projects_dir)
    owner_id = make_user(app, "owner@example.org")
    uid = make_user(app, "ana@example.org")
    with app.app_context():
        _, token = users.create_invitation("example", "ana@example.org", "viewer", owner_id)

    client = app.test_client()
    resp = client.get(f"/invite/{token}")
    next_path = parse_qs(urlsplit(resp.headers["Location"]).query)["next"][0]
    resp = sign_in(client, next=next_path)
    assert resp.headers["Location"].endswith("/app/")
    assert "You have joined example" in client.get("/app/").get_data(as_text=True)
    with app.app_context():
        assert users.get_membership("example", uid)["role"] == "viewer"
