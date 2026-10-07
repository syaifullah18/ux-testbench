"""Auth routes for researcher accounts.

Only active in public mode (TESTBENCH_MODE=public). In internal mode these
routes return 404, so existing deployments are completely unaffected.
"""
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   session as cookie, url_for)

from . import limits, mail, moderation, oauth, users
from .i18n import translator
from .rate_limit import clear as clear_rate, is_rate_limited, record_failed_login

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
SESSION_COOKIE_KEY = "tb_auth_sid"
OAUTH_FLOW_KEY = "tb_oauth"


def is_public_mode():
    return os.environ.get("TESTBENCH_MODE", "internal") == "public"


def password_login_enabled():
    """PASSWORD_LOGIN=off leaves Google and GitHub as the only way in: no password form, no
    sign-up with a password, no reset or verification emails. On by default so existing
    accounts keep working until they have linked a provider."""
    return os.environ.get("PASSWORD_LOGIN", "on").strip().lower() not in ("off", "0", "false", "no")


def require_password_login():
    if not password_login_enabled():
        abort(404)


def signup_mode():
    """'open', 'invite' or 'closed'.

    A launch runs through these in order: 'invite' during a private beta, so only people we
    invited can create an account; 'open' once quotas and moderation have been exercised;
    'closed' if we ever need to stop the flow without taking the instance down.
    """
    mode = os.environ.get("SIGNUP_MODE", "open").lower()
    return mode if mode in ("open", "invite", "closed") else "open"


def require_public():
    if not is_public_mode():
        abort(404)


def current_user():
    """Returns the logged-in researcher user row, or None."""
    sid = cookie.get(SESSION_COOKIE_KEY)
    if not sid:
        return None
    return users.load_session_user(sid)


def require_login():
    user = current_user()
    if not user:
        return None, redirect(url_for("auth.login"))
    return user, None


def _safe_next_url(target):
    if not target:
        return None
    if "\\" in target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return target


def _base_url():
    domain = (
        current_app.config.get("APP_DOMAIN")
        or os.environ.get("APP_DOMAIN")
        or current_app.config.get("SERVER_NAME")
    )
    if domain:
        scheme = "https" if (current_app.config.get("SESSION_COOKIE_SECURE") or request.is_secure) else "http"
        return f"{scheme}://{domain}"
    return request.host_url.rstrip("/")


def _login_user(user):
    """Start a signed-in session for this user. The CSRF token is replaced so a token seen
    before signing in is useless after it; the rest of the cookie (participant progress, a
    pending invitation) is kept."""
    cookie[SESSION_COOKIE_KEY] = users.create_session(
        user["id"], ip=request.remote_addr, user_agent=request.headers.get("User-Agent", ""))
    cookie["csrf_token"] = secrets.token_urlsafe(32)
    users.update_last_login(user["id"])


def _accept_pending_invite(user):
    """Accepts the invitation this browser opened before signing in, when it was sent to this
    account's address. Returns the project slug, or None."""
    token = cookie.get("pending_invite")
    if not token:
        return None
    invite = users.get_invitation(token)
    if not invite or invite["email"].lower() != user["email"].lower():
        return None
    cookie.pop("pending_invite", None)
    return users.accept_invitation(token, user["id"])


def _auth_page_vars():
    """What the sign-in and sign-up pages need to offer Google, GitHub and/or a password."""
    return {"providers": oauth.enabled_providers(), "password_login": password_login_enabled(),
            "next_param": _safe_next_url(request.args.get("next")) or ""}


# ---------------------------------------------------------------- sign up

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    require_public()
    t = translator("en")
    errors = {}
    form = {"email": "", "name": ""}

    if current_user():
        return redirect(url_for("public.dashboard"))

    mode = signup_mode()
    invite = None
    # The landing page hero collects the address and hands it over, so the form opens with a
    # field already filled and its first step already behind the visitor. An invitation is a
    # stronger claim on the address than a query string, so it still wins.
    prefilled = request.args.get("email", "").strip()
    if prefilled and EMAIL_RE.match(prefilled):
        form["email"] = prefilled
    invite_token = request.values.get("invite") or cookie.get("pending_invite")
    if invite_token:
        invite = users.get_invitation(invite_token)
        if invite:
            form["email"] = invite["email"]
    if mode == "closed":
        return render_template("auth/message.html", t=t, project=None,
                               title="Sign-ups are closed",
                               message="This instance is not accepting new accounts at the moment.")
    if mode == "invite" and invite is None:
        return render_template("auth/message.html", t=t, project=None,
                               title="Invitation needed",
                               message=("This instance is in a private beta, so an account needs an "
                                        "invitation. Open the link you were sent, or get in touch."))

    if request.method == "POST":
        require_password_login()
        form["email"] = request.form.get("email", "").strip()
        form["name"] = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not EMAIL_RE.match(form["email"]):
            errors["email"] = t("auth.invalid_email")
        if not form["name"]:
            errors["name"] = t("auth.name_required")
        if len(password) < 10:
            errors["password"] = t("auth.password_too_short")
        elif confirm and password != confirm:
            # The form now ships a single password field with a reveal toggle instead of asking
            # twice, so `confirm` is usually absent. It is still checked when a client sends it,
            # which keeps older clients and the existing tests honest.
            errors["confirm"] = t("auth.passwords_mismatch")

        if is_rate_limited(request.remote_addr, "signup"):
            errors["email"] = t("auth.rate_limited")
        else:
            over_quota = limits.check_signup(request.remote_addr)
            if over_quota:
                errors["email"] = over_quota

        if not errors:
            user_id, problems = users.create_user(form["email"], form["name"], password)
            if "email_taken" in problems:
                errors["email"] = t("auth.signup_check_email")
            elif problems:
                for p in problems:
                    errors.setdefault("password", t(f"auth.{p}"))
            else:
                if invite and invite["email"].lower() == form["email"].lower():
                    # The invitation reached this address, so the address is already proven.
                    users.set_email_verified(user_id)
                    users.accept_invitation(invite_token, user_id)
                    cookie.pop("pending_invite", None)
                else:
                    token = users.create_token(user_id, "verify", hours=24)
                    mail.send_verification(form["email"], token, _base_url())
                moderation.record_signup(request.remote_addr)
                moderation.audit("account.signup", user_id=user_id, actor=form["email"],
                                 ip=request.remote_addr)
                return render_template("auth/signup_done.html", t=t, email=form["email"], project=None)

    if invite_token and invite:
        cookie["pending_invite"] = invite_token
    return render_template("auth/signup.html", t=t, errors=errors, form=form, project=None,
                           invite=invite, signup_mode=mode, **_auth_page_vars())


@bp.route("/verify/<token>")
def verify(token):
    require_public()
    require_password_login()
    t = translator("en")
    user = users.use_token(token, "verify")
    if user is None:
        return render_template("auth/message.html", t=t, project=None,
                               title=t("auth.verify_failed_title"), message=t("auth.verify_failed"))
    users.set_email_verified(user["id"])
    return render_template("auth/message.html", t=t, project=None,
                           title=t("auth.verify_ok_title"), message=t("auth.verify_ok"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    require_public()
    t = translator("en")
    errors = {}
    form = {"email": ""}

    next_url = _safe_next_url(request.args.get("next")) or url_for("public.dashboard")

    if current_user():
        return redirect(next_url)

    if request.method == "POST":
        require_password_login()
        form["email"] = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if is_rate_limited(request.remote_addr, "auth_login"):
            errors["email"] = t("auth.rate_limited")
        else:
            user = users.get_user_by_email(form["email"])
            if user and users.verify_password(user, password):
                if user["disabled_at"]:
                    errors["email"] = t("auth.account_disabled")
                else:
                    _login_user(user)
                    clear_rate(request.remote_addr, "auth_login")
                    return redirect(next_url)
            else:
                record_failed_login(request.remote_addr, "auth_login")
                errors["email"] = t("auth.login_failed")

    return render_template("auth/login.html", t=t, errors=errors, form=form, project=None,
                           **_auth_page_vars())


# ---------------------------------------------------------------- Google and GitHub

def _redirect_uri(provider):
    return _base_url() + url_for("auth.oauth_callback", provider=provider)


@bp.route("/login/<provider>")
def oauth_start(provider):
    """A plain GET link, not a form: CSP form-action 'self' would block a POST that redirects
    to the provider. Signing in, signing up and linking from /account all start here."""
    require_public()
    if not oauth.is_enabled(provider):
        abort(404)
    linking = bool(request.args.get("link")) and current_user() is not None
    default_next = url_for("auth.account") if linking else url_for("public.dashboard")
    state, verifier = oauth.new_state()
    cookie[OAUTH_FLOW_KEY] = {
        "provider": provider, "state": state, "verifier": verifier,
        "next": _safe_next_url(request.args.get("next")) or default_next,
        "intent": "link" if linking else "login",
    }
    return redirect(oauth.authorize_url(provider, _redirect_uri(provider), state, verifier))


def _oauth_failed(title, message, status=200):
    return render_template("auth/message.html", t=translator("en"), project=None,
                           title=title, message=message), status


@bp.route("/login/<provider>/callback")
def oauth_callback(provider):
    require_public()
    if not oauth.is_enabled(provider):
        abort(404)
    t = translator("en")
    label = oauth.PROVIDERS[provider]["label"]

    # The state is single-use: popped before anything else, so a replayed callback finds nothing.
    flow = cookie.pop(OAUTH_FLOW_KEY, None)
    state = request.args.get("state", "")
    if (not isinstance(flow, dict) or flow.get("provider") != provider
            or not state or not hmac.compare_digest(str(flow.get("state", "")), state)):
        return _oauth_failed("Sign-in expired",
                             "That sign-in link is no longer valid. Start again from the sign-in page.", 400)
    code = request.args.get("code")
    if request.args.get("error") or not code:
        return _oauth_failed("Sign-in cancelled", f"{label} did not sign you in. You can try again.")

    try:
        profile = oauth.fetch_profile(provider, code, _redirect_uri(provider), flow["verifier"])
    except oauth.OAuthError as exc:
        return _oauth_failed("Sign-in failed", str(exc), 502)
    email = profile["email"]
    if not profile["subject"] or not email or not profile["email_verified"]:
        return _oauth_failed("No verified email",
                             f"Your {label} account has no verified email address. Verify one with "
                             f"{label} and try again.")

    ip = request.remote_addr
    linked = users.find_identity(provider, profile["subject"])

    if flow.get("intent") == "link":
        me = current_user()
        if me is None:
            return redirect(url_for("auth.login"))
        if linked is not None and linked["id"] != me["id"]:
            return _oauth_failed("Already linked",
                                 f"That {label} account is already linked to another account here.")
        if linked is None:
            if any(i["provider"] == provider for i in users.list_identities(me["id"])):
                return _oauth_failed("Already linked",
                                     f"Another {label} account is already linked. Unlink it first.")
            users.link_identity(me["id"], provider, profile["subject"], email)
            moderation.audit("account.link", user_id=me["id"], actor=me["email"],
                             detail={"provider": provider}, ip=ip)
        flash(f"{label} is linked. You can sign in with it from now on.", "success")
        return redirect(flow["next"])

    user = linked
    if user is None:
        user = users.get_user_by_email(email)
        if user is not None:
            if not user["email_verified_at"]:
                # Someone signed up with this address and a password but never proved it was
                # theirs. The provider has now proved it belongs to this visitor, so that password,
                # and anyone signed in with it, must not keep access.
                users.clear_password(user["id"])
                users.revoke_all_sessions(user["id"])
                users.set_email_verified(user["id"])
            users.link_identity(user["id"], provider, profile["subject"], email)
            moderation.audit("account.link", user_id=user["id"], actor=email,
                             detail={"provider": provider}, ip=ip)
            user = users.get_user(user["id"])
        else:
            user, problem = _oauth_signup(provider, profile, ip)
            if problem:
                return _oauth_failed(*problem)

    if user["disabled_at"]:
        return _oauth_failed("Account disabled", t("auth.account_disabled"), 403)
    _login_user(user)
    joined = _accept_pending_invite(user)
    if joined:
        flash(f"You have joined {joined}.", "success")
        if flow["next"].startswith("/invite/"):   # already used; opening it again would say so
            return redirect(url_for("public.dashboard"))
    return redirect(flow["next"])


def _oauth_signup(provider, profile, ip):
    """Creates an account for a first-time visitor. Returns (user, None) or (None, (title, message))."""
    mode = signup_mode()
    token = cookie.get("pending_invite")
    invite = users.get_invitation(token) if token else None
    invited = invite is not None and invite["email"].lower() == profile["email"]
    if mode == "closed":
        return None, ("Sign-ups are closed", "This instance is not accepting new accounts at the moment.")
    if mode == "invite" and not invited:
        return None, ("Invitation needed",
                      "This instance is in a private beta, so an account needs an invitation sent to "
                      f"{profile['email']}. Open the link you were sent, or get in touch.")
    over_quota = (translator("en")("auth.rate_limited") if is_rate_limited(ip, "signup")
                  else limits.check_signup(ip))
    if over_quota:
        return None, ("Too many sign-ups", over_quota)

    name = profile["name"].strip() or profile["email"].split("@")[0]
    user_id, problems = users.create_user(profile["email"], name)
    if problems:
        return None, ("Sign-up failed", "This account could not be created. Please try again.")
    users.set_email_verified(user_id)
    users.link_identity(user_id, provider, profile["subject"], profile["email"])
    moderation.record_signup(ip)
    moderation.audit("account.signup", user_id=user_id, actor=profile["email"],
                     detail={"provider": provider}, ip=ip)
    return users.get_user(user_id), None


# ---------------------------------------------------------------- logout

@bp.route("/logout", methods=["POST"])
def logout():
    require_public()
    sid = cookie.pop(SESSION_COOKIE_KEY, None)
    users.revoke_session(sid)
    return redirect(url_for("auth.login"))


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    require_public()
    require_password_login()
    t, sent = translator("en"), False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        limited = is_rate_limited(request.remote_addr, "forgot")
        record_failed_login(request.remote_addr, "forgot")
        if not limited:
            user = users.get_user_by_email(email)
            if user:
                token = users.create_token(user["id"], "reset", hours=1)
                mail.send_password_reset(email, token, _base_url())
        sent = True
    return render_template("auth/forgot.html", t=t, sent=sent, project=None)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    require_public()
    require_password_login()
    t, errors = translator("en"), {}
    if request.method == "POST":
        password, confirm = request.form.get("password", ""), request.form.get("confirm", "")
        if len(password) < 10:
            errors["password"] = t("auth.password_too_short")
        elif password != confirm:
            errors["confirm"] = t("auth.passwords_mismatch")
        if not errors:
            user = users.use_token(token, "reset")
            if user is None:
                return render_template("auth/message.html", t=t, project=None,
                                       title=t("auth.reset_failed_title"), message=t("auth.reset_failed"))
            users.update_password(user["id"], password)
            users.revoke_all_sessions(user["id"])
            return render_template("auth/message.html", t=t, project=None,
                                   title=t("auth.reset_ok_title"), message=t("auth.reset_ok"))
    return render_template("auth/reset.html", t=t, errors=errors, token=token, project=None)


@bp.route("/account", methods=["GET", "POST"])
def account():
    require_public()
    t = translator("en")
    user, early = require_login()
    if early:
        return early

    saved, errors = False, {}
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_profile":
            name = request.form.get("name", "").strip()
            if not name:
                errors["name"] = t("auth.name_required")
            else:
                users.update_user(user["id"], name=name)
                saved = True
        elif action == "change_password" and password_login_enabled() and users.has_password(user):
            current, new_pw, confirm = (request.form.get("current_password", ""),
                                        request.form.get("new_password", ""),
                                        request.form.get("confirm_password", ""))
            if not users.verify_password(user, current):
                errors["current_password"] = t("auth.wrong_password")
            elif len(new_pw) < 10:
                errors["new_password"] = t("auth.password_too_short")
            elif new_pw != confirm:
                errors["confirm_password"] = t("auth.passwords_mismatch")
            else:
                users.update_password(user["id"], new_pw)
                saved = True
        elif action == "unlink_identity":
            provider = request.form.get("provider", "")
            others = [i for i in users.list_identities(user["id"]) if i["provider"] != provider]
            if not others and not (password_login_enabled() and users.has_password(user)):
                errors["identities"] = ("This is the only way you can sign in. Link another account "
                                        "before removing it.")
            else:
                users.unlink_identity(user["id"], provider)
                moderation.audit("account.unlink", user_id=user["id"], actor=user["email"],
                                 detail={"provider": provider}, ip=request.remote_addr)
                saved = True
        elif action == "revoke_all":
            users.revoke_all_sessions(user["id"])
            _login_user(user)
            saved = True
        elif action == "delete_account":
            if request.form.get("confirm_email", "").strip().lower() != user["email"]:
                errors["confirm_email"] = t("auth.confirm_email_mismatch")
            else:
                problem = _release_projects(user, request.form.get("projects_action", "keep"))
                if problem:
                    errors["confirm_email"] = problem
                else:
                    users.revoke_all_sessions(user["id"])
                    moderation.audit("account.delete", actor=user["email"], ip=request.remote_addr)
                    moderation.anonymise_user(user["id"])
                    users.delete_user(user["id"])
                    cookie.pop(SESSION_COOKIE_KEY, None)
                    return redirect(url_for("auth.login"))
        user = users.get_user(user["id"])

    sessions = users.list_sessions(user["id"])
    projects = users.user_projects(user["id"])
    identities = {i["provider"]: i for i in users.list_identities(user["id"])}
    return render_template("auth/account.html", t=t, user=user, errors=errors,
                           saved=saved, sessions=sessions, projects=projects, project=None,
                           sole_owned=sole_owned(user), identities=identities,
                           providers=oauth.enabled_providers(),
                           password_login=password_login_enabled(),
                           has_password=users.has_password(user))


# ---------------------------------------------------------------- account data and deletion


def sole_owned(user):
    """Projects this account owns alone. Deleting the account must decide their fate: nobody
    else can reach a study whose only owner is gone."""
    out = []
    for m in users.user_projects(user["id"]):
        if m["role"] != "owner":
            continue
        slug = m["project_slug"]
        others = [x for x in users.project_members(slug)
                  if x["role"] == "owner" and x["user_id"] != user["id"]]
        if not others:
            out.append(slug)
    return out


def _release_projects(user, action):
    """Returns None when the account can be deleted, or a message explaining what to do first."""
    orphans = sole_owned(user)
    if not orphans:
        return None
    if action != "delete":
        return ("You are the only owner of: " + ", ".join(orphans) +
                ". Invite another owner and transfer them first, or tick "
                "“also delete these studies and their participant data”.")
    from . import db
    from .web import registry
    for slug in orphans:
        project = registry().get(slug)
        moderation.audit("project.delete_with_account", actor=user["email"], project_slug=slug)
        db.drop_study(slug)
        if project is not None and project.editable:
            import shutil
            shutil.rmtree(project.dir, ignore_errors=True)
        for member in users.project_members(slug):
            users.remove_membership(slug, member["user_id"])
    registry().refresh(force=True)
    return None


@bp.route("/account/export.json")
def export_account():
    """Everything this instance holds about the account itself. Participant data belongs to the
    studies and is exported per project as CSV."""
    require_public()
    user, early = require_login()
    if early:
        return early
    from flask import jsonify
    payload = {
        "account": {k: user[k] for k in ("id", "email", "name", "locale", "created_at",
                                         "last_login_at", "email_verified_at")},
        "memberships": [{"project": m["project_slug"], "role": m["role"], "since": m["created_at"]}
                        for m in users.user_projects(user["id"])],
        "sessions": [{"created_at": s["created_at"], "last_seen_at": s["last_seen_at"],
                      "user_agent": s["user_agent"]} for s in users.list_sessions(user["id"])],
        "linked_accounts": [{"provider": i["provider"], "email": i["email"], "since": i["created_at"]}
                            for i in users.list_identities(user["id"])],
    }
    resp = jsonify(payload)
    resp.headers["Content-Disposition"] = "attachment; filename=testbench-account.json"
    return resp
