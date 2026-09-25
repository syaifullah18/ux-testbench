"""Admin routes. Each project has its own admin passcode; SUPERADMIN_PASSCODE, when set,
opens every project."""
import csv
import io
import os

from flask import Blueprint, Response, abort, redirect, render_template, request, session as cookie, url_for

from . import storage
from .context import Ctx, is_admin
from .i18n import translator
from .web import get_project, passcode_ok, registry

bp = Blueprint("admin", __name__)


def grant(scope):
    granted = set(cookie.get("tb_admin") or [])
    granted.add(scope)
    cookie["tb_admin"] = sorted(granted)


def superadmin_passcode():
    return os.environ.get("SUPERADMIN_PASSCODE", "")


def admin_ctx(slug, mid=None):
    project = get_project(slug)
    if not is_admin(slug):
        return None, redirect(url_for("admin.dashboard", slug=slug))
    module = None
    if mid is not None:
        module = project.module(mid)
        if module is None:
            abort(404)
    return Ctx(project, module=module, admin=True), None


@bp.route("/admin/", methods=["GET", "POST"])
def overview():
    t = translator("en")
    error = None
    if request.method == "POST":
        if passcode_ok(superadmin_passcode(), request.form.get("passcode")):
            grant("*")
            return redirect(url_for("admin.overview"))
        error = t("admin.bad_passcode") if superadmin_passcode() else t("admin.super_not_configured")
    if "*" not in (cookie.get("tb_admin") or []):
        return render_template("admin/login.html", t=t, error=error, project=None)
    return render_template("admin/overview.html", t=t, projects=registry().projects.values(),
                           config_error=registry().last_error)


@bp.route("/<slug>/admin/", methods=["GET", "POST"])
def dashboard(slug):
    project = get_project(slug)
    t = translator(project.locale)
    if not is_admin(slug):
        error = None
        if request.method == "POST":
            given = request.form.get("passcode")
            if passcode_ok(project.admin_passcode, given):
                grant(slug)
                return redirect(url_for("admin.dashboard", slug=slug))
            if passcode_ok(superadmin_passcode(), given):
                grant("*")
                return redirect(url_for("admin.dashboard", slug=slug))
            error = t("admin.bad_passcode") if project.admin_passcode or superadmin_passcode() \
                else t("admin.not_configured", env=project.admin_passcode_env)
        return render_template("admin/login.html", t=t, error=error, project=project)
    ctx = Ctx(project, admin=True)
    stats = {}
    for m in project.modules.values():
        row = ctx.conn.execute("SELECT COUNT(*) AS started, COUNT(finished_at) AS finished FROM sessions WHERE module_id = ?",
                               (m.id,)).fetchone()
        stats[m.id] = dict(row)
    n = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    return ctx.render("admin/dashboard.html", stats=stats, n_participants=n,
                      reset_failed=request.args.get("reset") == "failed")


@bp.route("/<slug>/admin/logout")
def logout(slug):
    cookie.pop("tb_admin", None)
    return redirect(url_for("admin.dashboard", slug=slug))


@bp.route("/<slug>/admin/m/<mid>/")
def module_report(slug, mid):
    ctx, early = admin_ctx(slug, mid)
    return early or ctx.render("admin/module.html", body=ctx.module.impl.report(ctx))


@bp.route("/<slug>/admin/m/<mid>/export.csv")
def module_export(slug, mid):
    ctx, early = admin_ctx(slug, mid)
    if early:
        return early
    header, rows = ctx.module.impl.export(ctx)
    buf = io.StringIO()
    buf.write("﻿")  # BOM so spreadsheet apps read UTF-8 correctly
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={slug}-{mid}.csv"})


@bp.route("/<slug>/admin/m/<mid>/x/<path:path>")
@bp.route("/<slug>/admin/m/<mid>/x/", defaults={"path": ""})
def module_action(slug, mid, path):
    ctx, early = admin_ctx(slug, mid)
    return early or ctx.module.impl.admin_action(ctx, path) or abort(404)


@bp.route("/<slug>/admin/participants")
def participants(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    people = ctx.conn.execute("SELECT * FROM participants ORDER BY identity").fetchall()
    sessions = {}
    for s in ctx.conn.execute("SELECT participant_id, module_id, step, finished_at FROM sessions"):
        sessions.setdefault(s["participant_id"], {})[s["module_id"]] = s
    return ctx.render("admin/participants.html", people=people, sessions=sessions)


@bp.route("/<slug>/admin/p/<int:pid>", methods=["GET", "POST"])
def participant(slug, pid):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    person = ctx.conn.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if person is None:
        abort(404)
    ctx.participant = person
    sessions = {s["module_id"]: s for s in ctx.conn.execute("SELECT * FROM sessions WHERE participant_id = ?", (pid,))}
    if request.method == "POST":
        for mid, s in sessions.items():
            module = ctx.project.module(mid)
            if module:
                module.impl.save_detail(Ctx(ctx.project, module=module, participant=person, admin=True), s, request.form)
        ctx.conn.commit()
        return redirect(url_for("admin.participant", slug=slug, pid=pid, saved=1))
    blocks = []
    for m in ctx.project.modules.values():
        s = sessions.get(m.id)
        if s:
            mctx = Ctx(ctx.project, module=m, participant=person, admin=True)
            blocks.append({"m": m, "s": s, "html": m.impl.detail(mctx, s)})
    return ctx.render("admin/participant.html", person=person, blocks=blocks, saved=request.args.get("saved"))


@bp.post("/<slug>/admin/p/<int:pid>/delete")
def delete_participant(slug, pid):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    ctx.conn.execute("DELETE FROM participants WHERE id = ?", (pid,))
    ctx.conn.commit()
    return redirect(url_for("admin.participants", slug=slug))


@bp.post("/<slug>/admin/reset")
def reset(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    given = request.form.get("passcode")
    if not (passcode_ok(ctx.project.admin_passcode, given) or passcode_ok(superadmin_passcode(), given)):
        return redirect(url_for("admin.dashboard", slug=slug, reset="failed"))
    storage.reset(slug)
    return redirect(url_for("admin.dashboard", slug=slug))
