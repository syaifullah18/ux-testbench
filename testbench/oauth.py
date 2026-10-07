"""Sign in with Google or GitHub: the OAuth 2.0 authorization code flow with PKCE.

Standard library only. A provider is offered when both of its variables are set:
GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.

The flow never touches a password. We keep the provider's stable account id (`subject`) and
the address it vouches for; an address the provider has not verified is refused, because an
unverified address would let anyone claim an existing account.
"""
import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlencode

TIMEOUT = 10
USER_AGENT = "ux-testbench"

PROVIDERS = {
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "emails": "https://api.github.com/user/emails",
        "scope": "read:user user:email",
    },
}


class OAuthError(Exception):
    """The provider refused, or answered with something we cannot use. The message is safe to show."""


def _credentials(name):
    prefix = name.upper()
    return os.environ.get(f"{prefix}_CLIENT_ID", ""), os.environ.get(f"{prefix}_CLIENT_SECRET", "")


def enabled_providers():
    """[(name, label)] for every provider with both variables set, in a fixed order."""
    return [(name, spec["label"]) for name, spec in PROVIDERS.items() if all(_credentials(name))]


def is_enabled(name):
    return name in PROVIDERS and all(_credentials(name))


def new_state():
    """(state, code_verifier): both random, both kept in the signed cookie until the callback."""
    return secrets.token_urlsafe(32), secrets.token_urlsafe(48)


def _challenge(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(name, redirect_uri, state, verifier):
    spec = PROVIDERS[name]
    client_id, _ = _credentials(name)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": spec["scope"],
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    if name == "google":
        params["prompt"] = "select_account"
    return f"{spec['authorize']}?{urlencode(params)}"


def fetch_profile(name, code, redirect_uri, verifier):
    """Exchanges the code and returns {"subject", "email", "email_verified", "name"}."""
    spec = PROVIDERS[name]
    client_id, client_secret = _credentials(name)
    token = _post_form(spec["token"], {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    })
    access = token.get("access_token")
    if not access:
        raise OAuthError(f"{spec['label']} did not complete the sign-in. Please try again.")

    info = _get_json(spec["userinfo"], access)
    if name == "google":
        return {"subject": str(info.get("sub") or ""),
                "email": (info.get("email") or "").strip().lower(),
                "email_verified": info.get("email_verified") is True,
                "name": info.get("name") or ""}

    # GitHub: the profile's public email may be empty or unverified, so ask for the list.
    emails = _get_json(spec["emails"], access)
    primary = next((e for e in emails if isinstance(e, dict) and e.get("primary") and e.get("verified")),
                   None)
    return {"subject": str(info.get("id") or ""),
            "email": (primary["email"] if primary else "").strip().lower(),
            "email_verified": primary is not None,
            "name": info.get("name") or info.get("login") or ""}


# ---------------------------------------------------------------- HTTP (patched in tests)

def _request(req, label):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OAuthError(f"Could not reach {label}. Please try again.") from exc


def _post_form(url, data):
    req = urllib.request.Request(
        url, data=urlencode(data).encode(), method="POST",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"})
    return _request(req, url.split("/")[2])


def _get_json(url, access_token):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                      "Authorization": f"Bearer {access_token}"})
    return _request(req, url.split("/")[2])
