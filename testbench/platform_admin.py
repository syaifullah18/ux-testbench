"""Platform admin screens at /app/admin/, for whoever operates the instance.

These are deliberately separate from a project's own admin pages. A project admin reads their
own study's results; a platform admin acts on accounts and studies across the whole instance:
disabling an account, taking a study offline after an abuse report, approving a study for
/explore, and reading the audit log. Every action here is written to `platform_audit`.

Only accounts with `is_platform_admin` reach these routes, and only in public mode.
"""
import functools

from flask import (Blueprint, abort, flash, redirect, render_template, request, url_for)

from . import auth, limits, moderation, storage, users
from .i18n import translator
from .web import registry

bp = Blueprint("platform", __name__, url_prefix="/app/admin")


def require_platform_admin(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not auth.is_public_mode():
            abort(404)
        user = auth.current_user()
        if not user:
            return redirect(url_for("auth.login", next=request.path))
        if not user["is_platform_admin"]:
            abort(403)
        return view(user, *args, **kwargs)
    return wrapper


def _audit(user, action, slug="", detail=None):
    moderation.audit(action, user_id=user["id"], actor=user["email"], project_slug=slug,
                     detail=detail, ip=request.remote_addr)


def _shell(user, **kw):
    return dict(user=user, t=translator("en"), project=None,
                open_reports=moderation.count_open_reports(), **kw)


@bp.route("/")
@require_platform_admin
def home(user):
    reg = registry()
    all_users = users.list_users()
    return render_template("platform/home.html", **_shell(
        user,
        counts={"users": len(all_users),
                "verified": sum(1 for u in all_users if u["email_verified_at"]),
                "disabled": sum(1 for u in all_users if u["disabled_at"]),
                "projects": len(reg.projects),
                "offline": len(moderation.offline_slugs())},
        limits=limits.all_limits(),
        recent=moderation.list_audit(limit=12),
        config_error=reg.last_error))


# ---------------------------------------------------------------- accounts

@bp.route("/users")
@require_platform_admin
def user_list(user):
    rows = []
    for u in users.list_users():
        owned = [m["project_slug"] for m in users.user_projects(u["id"])]
        rows.append({"u": u, "projects": owned})
    return render_template("platform/users.html", **_shell(user, rows=rows))


@bp.post("/users/<int:user_id>/disable")
@require_platform_admin
def disable_user(user, user_id):
    target = users.get_user(user_id)
    if not target:
        abort(404)
    if target["id"] == user["id"]:
        flash("You cannot disable your own account.", "error")
        return redirect(url_for("platform.user_list"))
    enable = request.form.get("enable") == "1"
    users.update_user(user_id, disabled_at=None if enable else storage.now_iso())
    if not enable:
        users.revoke_all_sessions(user_id)   # a disabled account should lose its sessions now
    _audit(user, "user.enable" if enable else "user.disable", detail={"email": target["email"]})
    flash(f"{target['email']} {'enabled' if enable else 'disabled'}.", "success")
    return redirect(url_for("platform.user_list"))


@bp.post("/users/<int:user_id>/admin")
@require_platform_admin
def toggle_admin(user, user_id):
    target = users.get_user(user_id)
    if not target:
        abort(404)
    if target["id"] == user["id"]:
        flash("You cannot change your own admin rights.", "error")
        return redirect(url_for("platform.user_list"))
    grant = request.form.get("grant") == "1"
    users.update_user(user_id, is_platform_admin=1 if grant else 0)
    _audit(user, "user.admin_grant" if grant else "user.admin_revoke",
           detail={"email": target["email"]})
    flash(f"Platform admin {'granted to' if grant else 'revoked from'} {target['email']}.", "success")
    return redirect(url_for("platform.user_list"))


# ---------------------------------------------------------------- studies

@bp.route("/projects")
@require_platform_admin
def project_list(user):
    reg = registry()
    flags = moderation.flags_for(set(reg.projects))
    rows = []
    for p in reg.projects.values():
        members = users.project_members(p.slug)
        rows.append({"p": p, "flags": flags.get(p.slug),
                     "owners": [m["email"] for m in members if m["role"] == "owner"],
                     "members": len(members),
                     "participants": limits.participant_count(p.slug)})
    rows.sort(key=lambda r: r["p"].slug)
    return render_template("platform/projects.html", **_shell(user, rows=rows))


@bp.post("/projects/<slug>/offline")
@require_platform_admin
def set_offline(user, slug):
    if registry().get(slug) is None:
        abort(404)
    if request.form.get("online") == "1":
        moderation.put_online(slug, by_user_id=user["id"])
        _audit(user, "project.online", slug=slug)
        flash(f"{slug} is reachable again.", "success")
    else:
        reason = request.form.get("reason", "").strip()
        moderation.take_offline(slug, reason, by_user_id=user["id"])
        _audit(user, "project.offline", slug=slug, detail={"reason": reason})
        flash(f"{slug} is offline. Participants see a neutral notice; results stay readable.", "success")
    return redirect(url_for("platform.project_list"))


@bp.post("/projects/<slug>/explore")
@require_platform_admin
def set_explore(user, slug):
    if registry().get(slug) is None:
        abort(404)
    approved = request.form.get("approve") == "1"
    moderation.approve_explore(slug, approved, by_user_id=user["id"])
    _audit(user, "project.explore_approve" if approved else "project.explore_remove", slug=slug)
    flash(f"{slug} {'is now listed on /explore' if approved else 'removed from /explore'}.", "success")
    return redirect(url_for("platform.project_list"))


# ---------------------------------------------------------------- reports and audit

@bp.route("/reports")
@require_platform_admin
def report_list(user):
    status = request.args.get("status") or None
    if status and status not in moderation.REPORT_STATUSES:
        status = None
    return render_template("platform/reports.html", **_shell(
        user, reports=moderation.list_reports(status), status=status,
        statuses=moderation.REPORT_STATUSES))


@bp.post("/reports/<int:report_id>")
@require_platform_admin
def handle_report(user, report_id):
    status = request.form.get("status", "")
    if status not in moderation.REPORT_STATUSES:
        abort(400)
    moderation.set_report_status(report_id, status, handled_by=user["id"],
                                 note=request.form.get("note", ""))
    _audit(user, "report.handle", detail={"report": report_id, "status": status})
    flash(f"Report #{report_id} marked {status}.", "success")
    return redirect(url_for("platform.report_list"))


@bp.route("/audit")
@require_platform_admin
def audit_list(user):
    return render_template("platform/audit.html", **_shell(
        user, entries=moderation.list_audit(limit=300)))
