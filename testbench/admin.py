"""Admin routes. Each project has its own admin passcode; SUPERADMIN_PASSCODE, when set,
opens every project."""
import csv
import io
import os

from flask import Blueprint, Response, abort, redirect, render_template, request, session as cookie, url_for

from . import passcodes, storage
from .context import Ctx, is_admin, is_super
from .i18n import translator
from .web import get_project, registry
from .rate_limit import is_rate_limited, record_failed_login

bp = Blueprint("admin", __name__)


def grant(scope):
    granted = set(cookie.get("tb_admin") or [])
    granted.add(scope)
    cookie["tb_admin"] = sorted(granted)


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
        if is_rate_limited(request.remote_addr, "superadmin"):
            error = "Too many failed attempts. Try again later."
        elif passcodes.superadmin_ok(request.form.get("passcode")):
            grant("*")
            return redirect(url_for("admin.overview"))
        else:
            record_failed_login(request.remote_addr, "superadmin")
            error = t("admin.bad_passcode") if os.environ.get("SUPERADMIN_PASSCODE") else t("admin.super_not_configured")
    if not is_super():
        if os.environ.get("TESTBENCH_MODE", "internal") == "public":
            return redirect(url_for("auth.login"))
        return render_template("admin/login.html", t=t, error=error, project=None)
    from .i18n import available
    return render_template("admin/overview.html", t=t, projects=registry().projects.values(),
                           config_error=registry().last_error, locales=available(), studio_dir=registry().studio,
                           error=request.args.get("error"), notice=request.args.get("notice"))


@bp.route("/app/p/<slug>/", methods=["GET", "POST"])
@bp.route("/<slug>/admin/", methods=["GET", "POST"])
def dashboard(slug):
    project = get_project(slug)
    t = translator(project.locale)
    if not is_admin(slug):
        if os.environ.get("TESTBENCH_MODE", "internal") == "public":
            return redirect(url_for("auth.login"))
        error = None
        if request.method == "POST":
            given = request.form.get("passcode")
            if is_rate_limited(request.remote_addr, f"admin:{slug}"):
                error = "Too many failed attempts. Try again later."
            elif passcodes.check(project, "admin", given):
                grant(slug)
                return redirect(url_for("admin.dashboard", slug=slug))
            elif passcodes.superadmin_ok(given):
                grant("*")
                return redirect(url_for("admin.dashboard", slug=slug))
            else:
                record_failed_login(request.remote_addr, f"admin:{slug}")
                error = t("admin.bad_passcode") if passcodes.source(project, "admin") or os.environ.get("SUPERADMIN_PASSCODE") \
                    else t("admin.not_configured", env=project.admin_passcode_env)
        return render_template("admin/login.html", t=t, error=error, project=project)
    ctx = Ctx(project, admin=True)
    stats = {}
    for m in project.modules.values():
        row = ctx.conn.execute("SELECT COUNT(*) AS started, COUNT(finished_at) AS finished FROM sessions WHERE module_id = ?",
                               (m.id,)).fetchone()
        stats[m.id] = dict(row)
    
    n = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    started_any = ctx.conn.execute("SELECT COUNT(DISTINCT participant_id) FROM sessions").fetchone()[0]
    finished_any = ctx.conn.execute("SELECT COUNT(DISTINCT participant_id) FROM sessions WHERE finished_at IS NOT NULL").fetchone()[0]
    
    daily_rows = ctx.conn.execute("SELECT date(created_at) as d, COUNT(*) as c FROM participants GROUP BY d ORDER BY d DESC LIMIT 14").fetchall()
    daily_signups = [{"date": r["d"], "count": r["c"]} for r in daily_rows]
    daily_signups.reverse() # chronological
    
    return ctx.render("admin/dashboard.html", stats=stats, n_participants=n,
                      funnel={"total": n, "started": started_any, "finished": finished_any},
                      daily_signups=daily_signups,
                      reset_failed=request.args.get("reset") == "failed")


@bp.route("/app/p/<slug>/logout")
@bp.route("/<slug>/admin/logout")
def logout(slug):
    cookie.pop("tb_admin", None)
    return redirect(url_for("admin.dashboard", slug=slug))


@bp.route("/admin/logout")
def super_logout():
    cookie.pop("tb_admin", None)
    return redirect(url_for("admin.overview"))


@bp.route("/app/p/<slug>/m/<mid>/")
@bp.route("/<slug>/admin/m/<mid>/")
def module_report(slug, mid):
    ctx, early = admin_ctx(slug, mid)
    return early or ctx.render("admin/module.html", body=ctx.module.impl.report(ctx))


@bp.route("/app/p/<slug>/m/<mid>/export.csv")
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


@bp.route("/app/p/<slug>/m/<mid>/x/<path:path>")
@bp.route("/app/p/<slug>/m/<mid>/x/", defaults={"path": ""})
@bp.route("/<slug>/admin/m/<mid>/x/<path:path>")
@bp.route("/<slug>/admin/m/<mid>/x/", defaults={"path": ""})
def module_action(slug, mid, path):
    ctx, early = admin_ctx(slug, mid)
    return early or ctx.module.impl.admin_action(ctx, path) or abort(404)


@bp.route("/app/p/<slug>/export.csv")
@bp.route("/<slug>/admin/export.csv")
def project_export(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    participants = ctx.conn.execute("SELECT id, identity, created_at, last_seen_at FROM participants ORDER BY identity").fetchall()
    headers = ["participant", "created_at", "last_seen_at"]
    module_data = []
    for m in ctx.project.modules:
        sessions = ctx.conn.execute("SELECT id, participant_id FROM sessions WHERE module_id = ?", (m.id,)).fetchall()
        sid_to_pid = {s["id"]: s["participant_id"] for s in sessions}
        sids = list(sid_to_pid.keys())
        m_headers, sid_cols = m.impl.export_cols(ctx, sids)
        headers.extend(m_headers)
        pid_cols = {sid_to_pid[sid]: cols for sid, cols in sid_cols.items()}
        module_data.append(pid_cols)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for p in participants:
        row = [p["identity"], p["created_at"], p["last_seen_at"] or ""]
        p_cols = {}
        for m_cols in module_data:
            p_cols.update(m_cols.get(p["id"], {}))
        for h in headers[3:]:
            row.append(p_cols.get(h, ""))
        writer.writerow(row)
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={slug}_combined.csv"})


@bp.route("/app/p/<slug>/participants")
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


@bp.route("/app/p/<slug>/p/<int:pid>", methods=["GET", "POST"])
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


@bp.post("/app/p/<slug>/p/<int:pid>/delete")
@bp.post("/<slug>/admin/p/<int:pid>/delete")
def delete_participant(slug, pid):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    ctx.conn.execute("DELETE FROM participants WHERE id = ?", (pid,))
    storage.audit(ctx.conn, "delete_participant", request.remote_addr, {"participant_id": pid})
    ctx.conn.commit()
    return redirect(url_for("admin.participants", slug=slug))


@bp.post("/app/p/<slug>/reset")
@bp.post("/<slug>/admin/reset")
def reset(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        from . import auth, users
        if not users.can(auth.current_user(), slug, "reset"):
            return redirect(url_for("admin.dashboard", slug=slug, reset="failed"))
    else:
        given = request.form.get("passcode")
        if not (passcodes.check(ctx.project, "admin", given) or passcodes.superadmin_ok(given)):
            return redirect(url_for("admin.dashboard", slug=slug, reset="failed"))
    storage.reset(slug)
    storage.audit(ctx.conn, "reset_project", request.remote_addr, {})
    return redirect(url_for("admin.dashboard", slug=slug))
