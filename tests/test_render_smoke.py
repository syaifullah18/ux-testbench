"""Every page renders, signed out and signed in.

The template work that introduced the surface tokens touched almost every file in the tree. A
missing variable or a broken Jinja block in a page no other test opens would otherwise only
surface in front of a researcher, so this walks the routes and asks for a status code.
"""
import pytest
from testbench import users


PUBLIC_PATHS = ["/", "/about", "/privacy", "/terms", "/explore", "/report", "/docs/",
                "/docs/statistics", "/docs/configuration", "/login", "/signup",
                "/forgot", "/s/example/"]

SIGNED_IN_PATHS = ["/app/", "/app/new", "/account"]


@pytest.fixture
def public_app(projects_dir, make_app):
    return make_app(projects_dir, TESTBENCH_MODE="public")


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_pages_render(public_app, path):
    r = public_app.test_client().get(path, follow_redirects=True)
    assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_signed_in_pages_render(public_app):
    with public_app.app_context():
        uid, problems = users.create_user("smoke@example.org", "Smoke", "a-long-password")
        assert not problems
        users.set_email_verified(uid)

    c = public_app.test_client()
    c.post("/login", data={"email": "smoke@example.org", "password": "a-long-password"})
    for path in SIGNED_IN_PATHS:
        r = c.get(path, follow_redirects=True)
        assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_signup_prefills_the_address_the_landing_page_collected(public_app):
    body = public_app.test_client().get("/signup?email=lead@example.org").get_data(as_text=True)
    assert 'value="lead@example.org"' in body
    # And the rail ticks its first step rather than opening on an untouched form.
    assert "Verify email" in body


def test_signup_no_longer_demands_the_password_twice(public_app):
    c = public_app.test_client()
    assert "Confirm password" not in c.get("/signup").get_data(as_text=True)
    r = c.post("/signup", data={"name": "One Field", "email": "one@example.org",
                                "password": "a-long-password"}, follow_redirects=True)
    assert r.status_code == 200
    with public_app.app_context():
        assert users.get_user_by_email("one@example.org") is not None
