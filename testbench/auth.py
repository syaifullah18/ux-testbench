"""Auth routes for researcher accounts.

Only active in public mode (TESTBENCH_MODE=public). In internal mode these
routes return 404, so existing deployments are completely unaffected.
"""
import re

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session as cookie, url_for)

from . import limits, mail, moderation, users
from .i18n import translator
from .rate_limit import clear as clear_rate, is_rate_limited, record_failed_login

bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
SESSION_COOKIE_KEY = "tb_auth_sid"


def is_public_mode():
    import os
    return os.environ.get("TESTBENCH_MODE", "internal") == "public"


def signup_mode():
    """'open', 'invite' or 'closed'.

    A launch runs through these in order: 'invite' during a private beta, so only people we
    invited can create an account; 'open' once quotas and moderation have been exercised;
    'closed' if we ever need to stop the flow without taking the instance down.
    """
    import os
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


def _base_url():
    return request.host_url.rstrip("/")


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
        elif password != confirm:
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
                           invite=invite, signup_mode=mode)


@bp.route("/verify/<token>")
def verify(token):
    require_public()
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

    if current_user():
        return redirect(request.args.get("next") or url_for("public.dashboard"))

    if request.method == "POST":
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
                    sid = users.create_session(
                        user["id"], ip=request.remote_addr,
                        user_agent=request.headers.get("User-Agent", ""))
                    cookie[SESSION_COOKIE_KEY] = sid
                    users.update_last_login(user["id"])
                    clear_rate(request.remote_addr, "auth_login")
                    return redirect(request.args.get("next") or url_for("public.dashboard"))
            else:
                record_failed_login(request.remote_addr, "auth_login")
                errors["email"] = t("auth.login_failed")

    return render_template("auth/login.html", t=t, errors=errors, form=form, project=None)


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
    t, sent = translator("en"), False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not is_rate_limited(request.remote_addr, "forgot"):
            user = users.get_user_by_email(email)
            if user:
                token = users.create_token(user["id"], "reset", hours=1)
                mail.send_password_reset(email, token, _base_url())
        sent = True
    return render_template("auth/forgot.html", t=t, sent=sent, project=None)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    require_public()
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
        elif action == "change_password":
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
        elif action == "revoke_all":
            users.revoke_all_sessions(user["id"])
            sid = users.create_session(user["id"], ip=request.remote_addr,
                                       user_agent=request.headers.get("User-Agent", ""))
            cookie[SESSION_COOKIE_KEY] = sid
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
    return render_template("auth/account.html", t=t, user=user, errors=errors,
                           saved=saved, sessions=sessions, projects=projects, project=None,
                           sole_owned=sole_owned(user))


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
    from . import storage
    from .web import registry
    for slug in orphans:
        project = registry().get(slug)
        moderation.audit("project.delete_with_account", actor=user["email"], project_slug=slug)
        db = storage.db_path(slug)
        for extra in (db, db.with_suffix(".db-wal"), db.with_suffix(".db-shm")):
            if extra.exists():
                extra.unlink()
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
    }
    resp = jsonify(payload)
    resp.headers["Content-Disposition"] = "attachment; filename=testbench-account.json"
    return resp
