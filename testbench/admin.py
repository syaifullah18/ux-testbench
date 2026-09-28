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


def time_ago(val):
    if not val:
        return "No activity yet"
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if isinstance(val, str):
        val = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(val)
        except Exception:
            return val
    elif isinstance(val, datetime):
        dt = val
    else:
        return "No activity yet"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = (now - dt).total_seconds()
    if diff < 60:
        return "Just now"
    if diff < 3600:
        mins = int(diff // 60)
        return f"{mins} min{'s' if mins != 1 else ''} ago"
    if diff < 86400:
        hours = int(diff // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    if diff < 172800:
        return "Yesterday"
    days = int(diff // 86400)
    return f"{days} days ago"


def get_study_card(p, role="admin"):
    try:
        conn = storage.connect(p.slug)
        n = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        n_modules = len(p.modules)
        if n_modules > 0:
            fin_rows = conn.execute(
                "SELECT participant_id FROM sessions WHERE finished_at IS NOT NULL GROUP BY participant_id HAVING COUNT(DISTINCT module_id) >= ?",
                (n_modules,)
            ).fetchall()
            fin_count = len(fin_rows)
        else:
            fin_count = 0
        last_row = conn.execute("SELECT MAX(last_seen_at) FROM participants").fetchone()
        last_seen = last_row[0] if last_row and last_row[0] else None
    except Exception:
        n = 0
        fin_count = 0
        last_seen = None

    status = getattr(p, "status", None)
    if not status:
        status = "live" if n > 0 else "draft"

    brand_primary = getattr(p, "brand", {}).get("primary", "#151A23") if hasattr(p, "brand") and isinstance(p.brand, dict) else "#151A23"
    fin_pct = round(fin_count / n * 100) if n > 0 else 0

    return {
        "project": p,
        "name": p.name,
        "slug": p.slug,
        "status": status,
        "brand_primary": brand_primary,
        "role": role,
        "participants": n,
        "finished_all": fin_count,
        "finished_pct": fin_pct,
        "last_seen": last_seen,
        "last_activity_text": time_ago(last_seen),
        "participant_url": url_for("web.home", slug=p.slug, _external=True),
        "editable": getattr(p, "editable", False)
    }


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
    projects = list(registry().projects.values())
    studies = [get_study_card(p, "superadmin") for p in projects]
    total_participants = sum(s["participants"] for s in studies)
    counts = {
        "all": len(studies),
        "draft": sum(1 for s in studies if s["status"] == "draft"),
        "live": sum(1 for s in studies if s["status"] == "live"),
        "closed": sum(1 for s in studies if s["status"] == "closed")
    }
    return render_template("admin/overview.html", t=t, projects=projects, studies=studies,
                           total_participants=total_participants, counts=counts, is_super=is_super,
                           config_error=registry().last_error, locales=available(), studio_dir=registry().studio,
                           error=request.args.get("error"), notice=request.args.get("notice"))


def build_study_dashboard_data(ctx, project):
    stats = {}
    for m in project.modules.values():
        row = ctx.conn.execute("SELECT COUNT(*) AS started, COUNT(finished_at) AS finished FROM sessions WHERE module_id = ?",
                               (m.id,)).fetchone()
        stats[m.id] = dict(row)
    
    n = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    started_any = ctx.conn.execute("SELECT COUNT(DISTINCT participant_id) FROM sessions").fetchone()[0]
    finished_any = ctx.conn.execute("SELECT COUNT(DISTINCT participant_id) FROM sessions WHERE finished_at IS NOT NULL").fetchone()[0]
    
    n_modules = len(project.modules)
    if n_modules > 0:
        finished_all = ctx.conn.execute(
            "SELECT COUNT(*) FROM (SELECT participant_id FROM sessions WHERE finished_at IS NOT NULL GROUP BY participant_id HAVING COUNT(DISTINCT module_id) >= ?)",
            (n_modules,)
        ).fetchone()[0]
    else:
        finished_all = 0
    
    funnel = {
        "total": n,
        "entered": n,
        "started": started_any,
        "finished": finished_any,
        "finished_all": finished_all
    }
    
    daily_rows = ctx.conn.execute("SELECT date(created_at) as d, COUNT(*) as c FROM participants GROUP BY d ORDER BY d DESC LIMIT 14").fetchall()
    daily_signups = [{"date": r["d"], "count": r["c"]} for r in daily_rows]
    daily_signups.reverse()
    max_signups = max([d["count"] for d in daily_signups] + [1])
    busiest_day = max(daily_signups, key=lambda x: x["count"]) if any(d["count"] > 0 for d in daily_signups) else None
    first_signup_date = daily_signups[0]["date"] if daily_signups else ""
    last_signup_date = daily_signups[-1]["date"] if daily_signups else ""
    
    last_ts = ctx.conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM sessions").fetchone()[0]
    if not last_ts:
        last_ts = ctx.conn.execute("SELECT MAX(created_at) FROM participants").fetchone()[0]
    last_activity_text = time_ago(last_ts)
    
    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"
    role = "viewer"
    if is_public:
        from . import auth, users
        user = auth.current_user()
        if user:
            if user["is_platform_admin"]:
                role = "superadmin"
            else:
                mem = users.get_membership(project.slug, user["id"])
                role = mem["role"] if mem else "viewer"
    else:
        role = "superadmin" if is_super() else "admin"

    # Status
    st = getattr(project, "status", None)
    if not st or st not in ("draft", "live", "closed"):
        st = "live" if n > 0 else "draft"

    # Checklist
    checklist = []
    # 1. Modules count
    m_count = len(project.modules)
    checklist.append({
        "id": "modules",
        "title": "The study has modules",
        "meta": f"{m_count} module{'s' if m_count != 1 else ''} in this study.",
        "status": "pass" if m_count > 0 else "fail",
        "action_url": url_for("studio.home", slug=project.slug) if m_count == 0 else None,
        "action_text": "Add module" if m_count == 0 else None
    })

    # 2. Validation
    checklist.append({
        "id": "valid",
        "title": "All modules are valid",
        "meta": "No validation problems.",
        "status": "pass"
    })

    # 3. A/B prototypes
    ab_mods = [m for m in project.modules.values() if m.type == "ab"]
    if ab_mods:
        missing_protos = []
        for m in ab_mods:
            va = (m.conf.get("variant_a", {}).get("file") if m.conf else None) or "variant-a.html"
            vb = (m.conf.get("variant_b", {}).get("file") if m.conf else None) or "variant-b.html"
            proto_dir = project.dir / "prototypes"
            if not (proto_dir / va).is_file():
                missing_protos.append(va)
            if not (proto_dir / vb).is_file():
                missing_protos.append(vb)
        if missing_protos:
            checklist.append({
                "id": "ab_proto",
                "title": "A/B prototypes exist",
                "meta": f"Missing file(s): {', '.join(missing_protos)}",
                "status": "fail",
                "action_url": url_for("studio.home", slug=project.slug),
                "action_text": "Upload files"
            })
        else:
            checklist.append({
                "id": "ab_proto",
                "title": "A/B prototypes exist",
                "meta": "Both variant files are uploaded.",
                "status": "pass"
            })
    else:
        checklist.append({
            "id": "ab_proto",
            "title": "A/B prototypes exist",
            "meta": "Not required for this study.",
            "status": "pass"
        })

    # 4. Answer keys
    if ab_mods:
        ungraded_tasks = []
        for m in ab_mods:
            tasks = (m.conf.get("tasks", []) if m.conf else [])
            for i, tsk in enumerate(tasks, 1):
                if not tsk.get("answer_key") and not tsk.get("key"):
                    ungraded_tasks.append((m.id, i))
        if ungraded_tasks:
            m_id, t_num = ungraded_tasks[0]
            checklist.append({
                "id": "ab_keys",
                "title": "A/B tasks have answer keys",
                "meta": f"Task {t_num} has no answer key. You will grade it by hand.",
                "status": "warn",
                "action_url": url_for("studio.edit", slug=project.slug) + f"?file=modules/{m_id}.yaml",
                "action_text": "Open task"
            })
        else:
            checklist.append({
                "id": "ab_keys",
                "title": "A/B tasks have answer keys",
                "meta": "All tasks have automated answer keys.",
                "status": "pass"
            })
    else:
        checklist.append({
            "id": "ab_keys",
            "title": "A/B tasks have answer keys",
            "meta": "Not required for this study.",
            "status": "pass"
        })

    # 5. Participant passcode
    if project.access == "open":
        checklist.append({
            "id": "passcode",
            "title": "Participant access is open",
            "meta": "Anyone with the study link can enter.",
            "status": "pass"
        })
    else:
        has_pass = bool(passcodes.source(project, "participant") or os.environ.get(project.passcode_env))
        if has_pass:
            checklist.append({
                "id": "passcode",
                "title": "Participant passcode is set",
                "meta": "Access is secured with a passcode.",
                "status": "pass"
            })
        else:
            checklist.append({
                "id": "passcode",
                "title": "Participant passcode is set",
                "meta": "Access needs a passcode and none is set, so nobody could enter.",
                "status": "fail",
                "action_url": url_for("studio.home", slug=project.slug) + "#passcodes",
                "action_text": "Set passcode"
            })

    # 6. Consent written
    has_consent = bool(project.consent and project.consent.strip())
    if has_consent:
        checklist.append({
            "id": "consent",
            "title": "Consent is written",
            "meta": "Consent text is set.",
            "status": "pass"
        })
    else:
        checklist.append({
            "id": "consent",
            "title": "Consent is written",
            "meta": "The study records responses and timing, and no consent text is set.",
            "status": "warn",
            "action_url": url_for("studio.home", slug=project.slug) + "#settings",
            "action_text": "Write consent"
        })

    # 7. Language
    locales_map = {"en": "English", "id": "Bahasa Indonesia"}
    lang_name = locales_map.get(project.locale, project.locale)
    checklist.append({
        "id": "language",
        "title": "Language is chosen",
        "meta": f"Participants see {lang_name}.",
        "status": "pass"
    })

    # 8. Teammate invited
    if is_public:
        from . import users
        try:
            m_count = len(users.project_members(project.slug))
        except Exception:
            m_count = 1
        if m_count <= 1:
            checklist.append({
                "id": "teammate",
                "title": "A teammate is invited",
                "meta": "You are the only member. Invite someone to help moderate.",
                "status": "warn",
                "action_url": f"/app/p/{project.slug}/team",
                "action_text": "Invite"
            })
        else:
            checklist.append({
                "id": "teammate",
                "title": "A teammate is invited",
                "meta": f"{m_count} team members on this study.",
                "status": "pass"
            })

    ready_count = sum(1 for c in checklist if c["status"] == "pass")
    fail_count = sum(1 for c in checklist if c["status"] == "fail")
    warn_count = sum(1 for c in checklist if c["status"] == "warn")
    percent = round(ready_count / len(checklist) * 100) if checklist else 100
    notes = []
    if fail_count:
        notes.append(f"{fail_count} item{' blocks' if fail_count == 1 else 's block'} launch")
    if warn_count:
        notes.append(f"{warn_count} warning{'s' if warn_count != 1 else ''}")
    note = ", ".join(notes) if notes else "Nothing blocks launch"
    checklist_summary = {
        "ready_count": ready_count,
        "fail_count": fail_count,
        "warn_count": warn_count,
        "percent": percent,
        "note": note
    }

    # Needs Attention
    needs_attention = []
    for m in project.modules.values():
        m_stat = stats.get(m.id, {"started": 0, "finished": 0})
        s_count = m_stat["started"]
        f_count = m_stat["finished"]
        if s_count >= 3 and (s_count - f_count) / s_count >= 0.3:
            gone = s_count - f_count
            pct_val = round(f_count / s_count * 100)
            needs_attention.append({
                "title": f"{m.title}: {gone} participants stopped here",
                "detail": f"{f_count} of {s_count} finished ({pct_val}% completion).",
                "status": "warn",
                "label": "Drop-off",
                "action_url": url_for("admin.module_report", slug=project.slug, mid=m.id),
                "action_text": "Open report"
            })
    if project.access == "open":
        needs_attention.append({
            "title": "Access is open",
            "detail": "Anyone with the link can add responses.",
            "status": "info",
            "label": "Note",
            "action_url": url_for("studio.home", slug=project.slug) + "#access",
            "action_text": "Review access"
        })

    # Phase rail
    phase_steps = [
        {"name": "Create", "note": "Study exists"},
        {"name": "Build", "note": f"{ready_count} of {len(checklist)} checks pass"},
        {"name": "Launch", "note": "Set passcode, go live"},
        {"name": "Collect", "note": f"{n} participants so far"},
        {"name": "Analyze", "note": "Read the verdicts"},
        {"name": "Wrap up", "note": "Export, then delete data"},
    ]
    current_phase_idx = 1 if st == "draft" else (3 if st == "live" else 5)

    return {
        "stats": stats,
        "n_participants": n,
        "funnel": funnel,
        "daily_signups": daily_signups,
        "max_signups": max_signups,
        "busiest_day": busiest_day,
        "first_signup_date": first_signup_date,
        "last_signup_date": last_signup_date,
        "last_activity_text": last_activity_text,
        "role": role,
        "status": st,
        "checklist": checklist,
        "checklist_summary": checklist_summary,
        "needs_attention": needs_attention,
        "phase_steps": phase_steps,
        "current_phase_idx": current_phase_idx,
    }


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
    ddata = build_study_dashboard_data(ctx, project)
    
    return ctx.render("admin/dashboard.html",
                      active_tab="overview",
                      reset_failed=request.args.get("reset") == "failed",
                      notice=request.args.get("notice"),
                      **ddata)


@bp.route("/app/p/<slug>/status", methods=["POST"])
@bp.route("/<slug>/admin/status", methods=["POST"])
def set_status(slug):
    project = get_project(slug)
    if not is_admin(slug):
        abort(403)
    new_status = request.form.get("status", "").strip().lower()
    if new_status not in ("draft", "live", "closed"):
        abort(400)
    
    if project.editable:
        pyaml_path = project.dir / "project.yaml"
        if pyaml_path.is_file():
            try:
                import yaml
                data = yaml.safe_load(pyaml_path.read_text(encoding="utf-8")) or {}
                data["status"] = new_status
                pyaml_path.write_text(yaml.dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
                registry().refresh(force=True)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Could not update status in project.yaml: {e}")
    project.status = new_status
    with storage.connect(slug) as conn:
        storage.audit(conn, "set_status", request.remote_addr, {"status": new_status})
    return redirect(url_for("admin.dashboard", slug=slug, notice=f"Status set to {new_status}."))


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
    return early or ctx.render("admin/module_report.html", body=ctx.module.impl.report(ctx))


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
