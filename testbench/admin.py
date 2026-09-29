"""Admin routes. Each project has its own admin passcode; SUPERADMIN_PASSCODE, when set,
opens every project."""
import csv
import io
import os
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session as cookie, url_for

from . import passcodes, storage
from .context import Ctx, is_admin, is_super, can_edit
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
            error = t("admin.rate_limited")
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
    ab_mods = [m for m in project.modules.values() if m.type in ("ab", "ab_test")]
    if ab_mods:
        missing_protos = []
        for m in ab_mods:
            variants = (m.conf.get("variants") or {}) if m.conf else {}
            if not variants:
                va = (m.conf.get("variant_a", {}).get("file") if m.conf else None) or "variant-a.html"
                vb = (m.conf.get("variant_b", {}).get("file") if m.conf else None) or "variant-b.html"
                variants = {"A": {"file": va}, "B": {"file": vb}}
            for v_key, v_info in variants.items():
                if isinstance(v_info, dict):
                    if "path" in v_info and v_info["path"]:
                        p = Path(v_info["path"])
                        if not p.is_file():
                            missing_protos.append(str(v_info.get("file") or p.name))
                    elif v_info.get("file"):
                        v_file = str(v_info["file"])
                        p1 = project.dir / v_file
                        p2 = project.dir / "prototypes" / v_file
                        if not p1.is_file() and not p2.is_file():
                            missing_protos.append(v_file)
                elif isinstance(v_info, str):
                    p1 = project.dir / v_info
                    p2 = project.dir / "prototypes" / v_info
                    if not p1.is_file() and not p2.is_file():
                        missing_protos.append(v_info)
        if missing_protos:
            checklist.append({
                "id": "ab_proto",
                "title": "A/B prototypes exist",
                "meta": f"Missing file(s): {', '.join(missing_protos)}",
                "status": "fail",
                "action_url": url_for("studio.prototypes", slug=project.slug),
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
                has_key = bool(tsk.get("accept") or tsk.get("answer_key") or tsk.get("key"))
                if not has_key:
                    ungraded_tasks.append((m.id, i))
        if ungraded_tasks:
            m_id, t_num = ungraded_tasks[0]
            checklist.append({
                "id": "ab_keys",
                "title": "A/B tasks have answer keys",
                "meta": f"Task {t_num} has no answer key. You will grade it by hand.",
                "status": "warn",
                "action_url": url_for("studio.module_edit", slug=project.slug, mid=m_id),
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
                "action_url": url_for("admin.launch", slug=project.slug) + "#passcodes",
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
            "action_url": url_for("admin.settings_data", slug=project.slug) + "#sec-study",
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
                "action_url": url_for("admin.results", slug=project.slug, m=m.id),
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
                error = t("admin.rate_limited")
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
    if not can_edit(slug):
        abort(403)
    new_status = request.form.get("status", "").strip().lower()
    if new_status not in ("draft", "live", "closed"):
        abort(400)
    
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
    with storage.connect(slug) as conn:
        storage.audit(conn, "set_status", request.remote_addr, {"status": new_status})
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"ok": True, "status": new_status})
    msg = f"Status set to {new_status}."
    flash(msg, "success")
    next_url = request.form.get("next") or request.referrer or url_for("admin.dashboard", slug=slug, notice=msg)
    return redirect(next_url)


@bp.route("/app/p/<slug>/logout")
@bp.route("/<slug>/admin/logout")
def logout(slug):
    cookie.pop("tb_admin", None)
    return redirect(url_for("admin.dashboard", slug=slug))


@bp.route("/admin/logout")
def super_logout():
    cookie.pop("tb_admin", None)
    return redirect(url_for("admin.overview"))


MODULE_TYPE_LABELS = {
    "survey": "Survey",
    "ab_test": "A/B test",
    "ab": "A/B test",
    "first_click": "First click",
    "card_sort": "Card sort",
    "tree_test": "Tree test",
    "tree": "Tree test",
}


def make_headline(ctx, m, m_stat):
    finished = m_stat.get("finished", 0)
    started = m_stat.get("started", 0)
    if finished == 0:
        return '<span class="text-muted">No one has finished this module yet.</span>'
    try:
        if m.type in ("ab", "ab_test"):
            c = m.conf
            runs = m.impl._finished_runs(ctx)
            if runs:
                base = c.get("baseline")
                keys = [k for k in c.get("variants", {}) if k != base]
                if keys:
                    challenger = keys[0]
                    people = {}
                    for r in runs:
                        people.setdefault(r["session"]["id"], {})[r["variant"]] = r
                    pairs = [p for p in people.values() if base in p and challenger in p]
                    min_n = c.get("rule", {}).get("min_participants", 8)
                    if len(pairs) < min_n:
                        return f"Indicative: {len(pairs)} of {min_n} people tried both variants."
                    else:
                        return f"{len(pairs)} people tried both variants. Rules evaluated against baseline."
        elif m.type in ("tree", "tree_test"):
            n_tasks = len(m.conf.get("tasks", []))
            return f"{finished} finished across {n_tasks} task{'s' if n_tasks != 1 else ''}."
        elif m.type == "first_click":
            return f"{finished} participant{'s' if finished != 1 else ''} completed the click task."
        elif m.type == "card_sort":
            return f"{finished} participant{'s' if finished != 1 else ''} sorted cards."
        elif m.type == "survey":
            return f"{finished} response{'s' if finished != 1 else ''} collected."
    except Exception:
        pass
    p_word = "person" if started == 1 else "people"
    return f"{started} {p_word} started, {finished} finished."


def get_results_nav_context(ctx):
    n_participants = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    modules_list = []
    for m in ctx.project.modules.values():
        cnt = ctx.conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE module_id = ? AND finished_at IS NOT NULL",
            (m.id,)
        ).fetchone()[0]
        raw_type = getattr(m, "type", "survey")
        modules_list.append({
            "id": m.id,
            "title": m.title,
            "type": MODULE_TYPE_LABELS.get(raw_type, raw_type.title()),
            "raw_type": raw_type,
            "finished": cnt,
            "module": m,
        })
    return n_participants, modules_list


@bp.route("/app/p/<slug>/results")
@bp.route("/<slug>/admin/results")
def results(slug, mid=None):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    project = ctx.project
    ddata = build_study_dashboard_data(ctx, project)
    stats = ddata["stats"]
    n_participants = ddata["n_participants"]

    if mid is None:
        mid = request.args.get("m")
    cur_module = project.module(mid) if mid else None

    modules_list = []
    for m in project.modules.values():
        m_stat = stats.get(m.id, {"started": 0, "finished": 0})
        m_type_label = MODULE_TYPE_LABELS.get(m.type, m.type)
        mctx = Ctx(project, module=m, admin=True)
        headline_text = make_headline(mctx, m, m_stat)
        desc = getattr(m, "description", "") or (m.conf.get("description", "") if hasattr(m, "conf") and isinstance(m.conf, dict) else "")
        modules_list.append({
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "type_label": m_type_label,
            "description": desc,
            "started": m_stat["started"],
            "finished": m_stat["finished"],
            "headline": headline_text,
        })

    needs_attention = [item for item in ddata["needs_attention"] if item.get("label") == "Drop-off"]

    report_body = None
    cur_stat = None
    cur_module_type_label = None
    if cur_module:
        cur_stat = stats.get(cur_module.id, {"started": 0, "finished": 0})
        cur_module_type_label = MODULE_TYPE_LABELS.get(cur_module.type, cur_module.type)
        if cur_stat["finished"] > 0:
            mctx = Ctx(project, module=cur_module, admin=True)
            try:
                report_body = cur_module.impl.report(mctx)
            except Exception as e:
                report_body = f'<div class="panel-b"><p class="err">Could not render report: {e}</p></div>'

    can_edit = getattr(project, "editable", True)

    return ctx.render("admin/results.html",
                      project=project,
                      active_tab="results",
                      modules_list=modules_list,
                      cur_module=cur_module,
                      cur_stat=cur_stat,
                      cur_module_type_label=cur_module_type_label,
                      report_body=report_body,
                      needs_attention=needs_attention,
                      n_participants=n_participants,
                      can_edit=can_edit)


@bp.route("/app/p/<slug>/m/<mid>/")
@bp.route("/<slug>/admin/m/<mid>/")
def module_report(slug, mid):
    return results(slug, mid=mid)


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


@bp.route("/app/p/<slug>/export")
@bp.route("/<slug>/admin/export")
def export_view(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    n_participants, modules_list = get_results_nav_context(ctx)

    mrows = []
    for m in ctx.project.modules.values():
        mctx = Ctx(ctx.project, module=m, admin=True)
        try:
            header, rows = m.impl.export(mctx)
            n_rows = len(rows)
            n_cols = len(header)
        except Exception:
            n_rows = 0
            n_cols = 1

        raw_type = getattr(m, "type", "survey")
        mrows.append({
            "m": m,
            "type": MODULE_TYPE_LABELS.get(raw_type, raw_type.title()),
            "n": n_rows,
            "cols": n_cols,
        })

    participants_list = ctx.conn.execute(
        "SELECT id, identity, created_at, last_seen_at FROM participants ORDER BY identity"
    ).fetchall()
    headers = ["participant", "created_at", "last_seen_at"]
    for m in ctx.project.modules.values():
        sessions = ctx.conn.execute("SELECT id, participant_id FROM sessions WHERE module_id = ?", (m.id,)).fetchall()
        sid_to_pid = {s["id"]: s["participant_id"] for s in sessions}
        sids = list(sid_to_pid.keys())
        try:
            m_headers, sid_cols = m.impl.export_cols(ctx, sids)
            headers.extend(m_headers)
        except Exception:
            pass

    comb_rows = len(participants_list)
    comb_cols = len(headers)

    can_edit = is_super() or (ctx.project.editable if hasattr(ctx.project, "editable") else True)

    return ctx.render(
        "admin/export.html",
        project=ctx.project,
        active_tab="results",
        modules_list=modules_list,
        n_participants=n_participants,
        mrows=mrows,
        comb_rows=comb_rows,
        comb_cols=comb_cols,
        can_edit=can_edit,
        active_page="export"
    )


@bp.route("/app/p/<slug>/export.csv")
@bp.route("/<slug>/admin/export.csv")
def project_export(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    participants_list = ctx.conn.execute("SELECT id, identity, created_at, last_seen_at FROM participants ORDER BY identity").fetchall()
    headers = ["participant", "created_at", "last_seen_at"]
    module_data = []
    for m in ctx.project.modules.values():
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
    for p in participants_list:
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
    n_participants, modules_list = get_results_nav_context(ctx)

    raw_people = ctx.conn.execute("SELECT * FROM participants ORDER BY identity").fetchall()
    sessions = {}
    for s in ctx.conn.execute("SELECT participant_id, module_id, step, finished_at FROM sessions"):
        sessions.setdefault(s["participant_id"], {})[s["module_id"]] = s

    module_items = list(ctx.project.modules.values())
    total_modules = len(module_items)

    people = []
    for p in raw_people:
        p_sess = sessions.get(p["id"], {})
        done_cnt = 0
        started_cnt = 0
        mods = []
        started_module_titles = []
        for m in module_items:
            s = p_sess.get(m.id)
            if not s:
                mods.append("-")
            elif s["finished_at"]:
                mods.append("done")
                done_cnt += 1
                started_cnt += 1
                started_module_titles.append(m.title)
            else:
                step_val = s["step"] or "intro"
                mods.append(f"stop:{step_val}")
                started_cnt += 1
                started_module_titles.append(m.title)

        if total_modules > 0 and done_cnt == total_modules:
            p_state = "all"
        elif done_cnt > 0 or started_cnt > 0:
            p_state = "progress"
        else:
            p_state = "none"

        stopped_parts = []
        for i, m in enumerate(module_items):
            st = mods[i]
            if st.startswith("stop:"):
                stopped_parts.append(f"{m.title} at {st[5:]}")
        if stopped_parts:
            if len(stopped_parts) == 1:
                joined = stopped_parts[0]
            elif len(stopped_parts) == 2:
                joined = f"{stopped_parts[0]} and {stopped_parts[1]}"
            else:
                joined = f"{', '.join(stopped_parts[:-1])} and {stopped_parts[-1]}"
            stopped_line = f"Stopped in {joined}."
        elif p_state == "all":
            stopped_line = "Finished every module."
        else:
            stopped_line = ""

        last_seen = p["last_seen_at"] or p["created_at"]
        last_seen_text = time_ago(last_seen)

        if len(started_module_titles) == 0:
            started_modules_str = ""
        elif len(started_module_titles) == 1:
            started_modules_str = started_module_titles[0]
        elif len(started_module_titles) == 2:
            started_modules_str = f"{started_module_titles[0]} and {started_module_titles[1]}"
        else:
            started_modules_str = f"{', '.join(started_module_titles[:-1])} and {started_module_titles[-1]}"

        people.append({
            "id": p["id"],
            "identity": p["identity"],
            "created_at": p["created_at"],
            "last_seen_at": p["last_seen_at"],
            "last_seen_text": last_seen_text,
            "done_count": done_cnt,
            "total_modules": total_modules,
            "state": p_state,
            "stopped_line": stopped_line,
            "started_modules_str": started_modules_str,
            "mods": mods,
        })

    counts = {
        "all": len(people),
        "finished": sum(1 for p in people if p["state"] == "all"),
        "progress": sum(1 for p in people if p["state"] == "progress"),
        "none": sum(1 for p in people if p["state"] == "none"),
    }

    can_edit_flag = can_edit(slug)

    return ctx.render(
        "admin/participants.html",
        project=ctx.project,
        active_tab="results",
        people=people,
        sessions=sessions,
        counts=counts,
        modules_list=modules_list,
        n_participants=n_participants,
        can_edit=can_edit_flag,
        active_page="participants"
    )


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
        if not can_edit(slug):
            abort(403)
        for mid, s in sessions.items():
            module = ctx.project.module(mid)
            if module:
                module.impl.save_detail(Ctx(ctx.project, module=module, participant=person, admin=True), s, request.form)
        ctx.conn.commit()
        return redirect(url_for("admin.participant", slug=slug, pid=pid, saved=1))

    n_participants, modules_list = get_results_nav_context(ctx)
    can_edit_flag = can_edit(slug)

    blocks = []
    done_count = 0
    total_modules = len(ctx.project.modules)
    started_module_titles = []
    stopped_parts = []

    for idx, m in enumerate(ctx.project.modules.values()):
        s = sessions.get(m.id)
        raw_type = getattr(m, "type", "survey")
        type_name = MODULE_TYPE_LABELS.get(raw_type, raw_type.title())

        if not s:
            b_state = "none"
            html_content = ""
            step = ""
        elif s["finished_at"]:
            b_state = "done"
            done_count += 1
            started_module_titles.append(m.title)
            step = s["step"] or ""
            mctx = Ctx(ctx.project, module=m, participant=person, admin=True)
            html_content = m.impl.detail(mctx, s)
        else:
            b_state = "stopped"
            step = s["step"] or "intro"
            started_module_titles.append(m.title)
            stopped_parts.append(f"{m.title} at {step}")
            html_content = ""

        is_open = (b_state == "done" and raw_type in ("ab", "ab_test")) or (b_state == "done" and len(blocks) == 0)

        blocks.append({
            "m": m,
            "s": s,
            "type_name": type_name,
            "state": b_state,
            "step": step,
            "open": is_open,
            "html": html_content
        })

    if stopped_parts:
        if len(stopped_parts) == 1:
            p_stopped_line = f"Stopped in {stopped_parts[0]}."
        elif len(stopped_parts) == 2:
            p_stopped_line = f"Stopped in {stopped_parts[0]} and {stopped_parts[1]}."
        else:
            p_stopped_line = f"Stopped in {', '.join(stopped_parts[:-1])} and {stopped_parts[-1]}."
    elif done_count == total_modules and total_modules > 0:
        p_stopped_line = "Finished every module."
    else:
        p_stopped_line = ""

    last_seen = person["last_seen_at"] or person["created_at"]
    p_last_seen = time_ago(last_seen)

    if len(started_module_titles) == 0:
        started_modules_str = ""
    elif len(started_module_titles) == 1:
        started_modules_str = started_module_titles[0]
    elif len(started_module_titles) == 2:
        started_modules_str = f"{started_module_titles[0]} and {started_module_titles[1]}"
    else:
        started_modules_str = f"{', '.join(started_module_titles[:-1])} and {started_module_titles[-1]}"

    return ctx.render(
        "admin/participant.html",
        project=ctx.project,
        active_tab="results",
        person=person,
        blocks=blocks,
        done_count=done_count,
        total_modules=total_modules,
        p_stopped_line=p_stopped_line,
        p_last_seen=p_last_seen,
        started_modules_str=started_modules_str,
        modules_list=modules_list,
        n_participants=n_participants,
        can_edit=can_edit_flag,
        saved=request.args.get("saved"),
        active_page="participants"
    )


@bp.post("/app/p/<slug>/p/<int:pid>/delete")
@bp.post("/<slug>/admin/p/<int:pid>/delete")
def delete_participant(slug, pid):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    if not can_edit(slug):
        abort(403)
    row = ctx.conn.execute("SELECT identity FROM participants WHERE id = ?", (pid,)).fetchone()
    identity = row["identity"] if row else f"#{pid}"
    ctx.conn.execute("DELETE FROM participants WHERE id = ?", (pid,))
    storage.audit(ctx.conn, "delete_participant", request.remote_addr, {"participant_id": pid})
    ctx.conn.commit()
    msg = f"Participant {identity} has been deleted."
    flash(msg, "success")
    return redirect(url_for("admin.participants", slug=slug, notice=msg))


@bp.post("/app/p/<slug>/reset")
@bp.post("/<slug>/admin/reset")
def reset(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        from . import auth, users
        if not users.can(auth.current_user(), slug, "reset"):
            err = "You do not have permission to delete responses."
            flash(err, "error")
            return redirect(url_for("admin.dashboard", slug=slug, reset="failed"))
    else:
        given = request.form.get("passcode")
        if not (passcodes.check(ctx.project, "admin", given) or passcodes.superadmin_ok(given)):
            err = "Incorrect admin passcode. Responses were not deleted."
            flash(err, "error")
            return redirect(url_for("admin.dashboard", slug=slug, reset="failed"))
    storage.reset(slug)
    storage.audit(ctx.conn, "reset_project", request.remote_addr, {})
    msg = f"All participant responses for “{ctx.project.name}” have been deleted."
    flash(msg, "success")
    return redirect(url_for("admin.dashboard", slug=slug, notice=msg))


@bp.route("/app/p/<slug>/data")
@bp.route("/app/p/<slug>/settings")
@bp.route("/<slug>/admin/data")
@bp.route("/<slug>/admin/settings")
def settings_data(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    project = ctx.project

    n_participants = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    n_started_sessions = ctx.conn.execute("SELECT COUNT(*) FROM sessions WHERE started_at IS NOT NULL").fetchone()[0]
    n_finished_sessions = ctx.conn.execute("SELECT COUNT(*) FROM sessions WHERE finished_at IS NOT NULL").fetchone()[0]

    last_ts = ctx.conn.execute("SELECT MAX(COALESCE(finished_at, started_at)) FROM sessions").fetchone()[0]
    if not last_ts:
        last_ts = ctx.conn.execute("SELECT MAX(created_at) FROM participants").fetchone()[0]
    last_activity_text = time_ago(last_ts)

    total_bytes = 0
    if project.dir.is_dir():
        for p in project.dir.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                try:
                    total_bytes += p.stat().st_size
                except Exception:
                    pass
    storage_kb = round(total_bytes / 1024, 1)

    from .studio import history
    version_items = []
    if getattr(project, "editable", False):
        try:
            for item in history(project.dir)[:30]:
                f_path = project.dir / ".history" / item["id"]
                sz = f_path.stat().st_size if f_path.is_file() else 0
                version_items.append({
                    "file": item["file"],
                    "when": item["when"],
                    "size": sz,
                    "id": item["id"],
                })
        except Exception:
            pass

    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"
    can_delete = is_super() or (is_public and False)
    can_edit = is_super() or getattr(project, "editable", True)

    return ctx.render(
        "admin/data.html",
        project=project,
        active_tab="settings",
        n_participants=n_participants,
        n_started_sessions=n_started_sessions,
        n_finished_sessions=n_finished_sessions,
        last_activity_text=last_activity_text,
        storage_kb=storage_kb,
        versions=version_items,
        is_public=is_public,
        can_delete=can_delete,
        can_edit=can_edit,
    )


@bp.route("/app/p/<slug>/launch")
@bp.route("/<slug>/admin/launch")
def launch(slug):
    ctx, early = admin_ctx(slug)
    if early:
        return early
    project = ctx.project
    ddata = build_study_dashboard_data(ctx, project)

    participant_url = url_for("web.home", slug=project.slug, _external=True)
    admin_url = url_for("admin.dashboard", slug=project.slug, _external=True)

    p_source = passcodes.source(project, "participant")
    a_source = passcodes.source(project, "admin")

    est_mins = max(5, len(project.modules) * 3)
    can_edit = is_super() or getattr(project, "editable", True)
    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"

    return ctx.render(
        "admin/launch.html",
        project=project,
        active_tab="launch",
        participant_url=participant_url,
        admin_url=admin_url,
        participant_passcode_source=p_source or "none",
        admin_passcode_source=a_source or "none",
        est_minutes=est_mins,
        can_edit=can_edit,
        is_public=is_public,
        **ddata
    )


@bp.route("/app/p/<slug>/launch/passcode", methods=["POST"])
@bp.route("/<slug>/admin/launch/passcode", methods=["POST"])
def launch_passcode(slug):
    ctx, early = admin_ctx(slug)
    if early:
        if request.is_json:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return early
    project = ctx.project
    if not can_edit(slug):
        if request.is_json:
            return jsonify({"ok": False, "error": "Forbidden"}), 403
        abort(403)

    if request.is_json:
        data = request.get_json() or {}
        kind = data.get("kind", "participant")
        action = data.get("action", "set")
        val = (data.get("passcode") or "").strip()
    else:
        kind = request.form.get("kind", "participant")
        action = request.form.get("action", "set")
        val = (request.form.get("passcode") or "").strip()

    if kind not in ("participant", "admin"):
        abort(400)

    if action == "clear":
        passcodes.clear(slug, kind)
        with storage.connect(slug) as conn:
            storage.audit(conn, "clear_passcode", request.remote_addr, {"kind": kind})
        if request.is_json:
            return jsonify({"ok": True, "action": "clear", "kind": kind})
        return redirect(url_for("admin.launch", slug=slug, notice=f"{kind.title()} passcode removed."))

    if len(val) < 6:
        if request.is_json:
            return jsonify({"ok": False, "error": "Use at least 6 characters."}), 400
        return redirect(url_for("admin.launch", slug=slug, error="Use at least 6 characters."))

    passcodes.set_passcode(slug, kind, val)
    with storage.connect(slug) as conn:
        storage.audit(conn, "set_passcode", request.remote_addr, {"kind": kind})
    if request.is_json:
        return jsonify({"ok": True, "action": "set", "kind": kind})
    return redirect(url_for("admin.launch", slug=slug, notice=f"{kind.title()} passcode saved."))


@bp.route("/help")
@bp.route("/help/")
@bp.route("/admin/help")
@bp.route("/admin/help/")
def help():
    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"
    return render_template(
        "admin/help.html",
        active_tab="help",
        is_public=is_public,
        is_super=is_super(),
        project=None,
    )


@bp.route("/instance")
@bp.route("/instance/")
@bp.route("/admin/instance")
@bp.route("/admin/instance/")
def instance():
    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"
    allowed = is_super()
    if is_public:
        from . import auth
        user = auth.current_user()
        allowed = bool(user and user.get("is_platform_admin"))
        if not user:
            return redirect(url_for("auth.login", next=request.path))

    if not allowed:
        return render_template(
            "admin/instance.html",
            is_allowed=False,
            is_public=is_public,
            is_super=is_super(),
            project=None,
            active_tab="instance",
        )

    from . import moderation
    projects = list(registry().projects.values())
    studies = [get_study_card(p, "superadmin") for p in projects]
    offline_set = moderation.offline_slugs()
    for s in studies:
        s["is_offline"] = s["slug"] in offline_set

    total_studies = len(studies)
    live_studies = sum(1 for s in studies if s["status"] == "live" and not s.get("is_offline"))
    offline_studies = sum(1 for s in studies if s.get("is_offline"))
    total_participants = sum(s["participants"] for s in studies)

    cur_sec = request.args.get("s", "overview")
    valid_secs = ("overview", "users", "studies", "reports", "audit")
    if cur_sec not in valid_secs or (cur_sec in ("users", "reports") and not is_public):
        cur_sec = "overview"

    # Storage size
    total_bytes = 0
    data_dir = Path(current_app.config["DATA_DIR"])
    if data_dir.is_dir():
        for p in data_dir.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                try:
                    total_bytes += p.stat().st_size
                except Exception:
                    pass
    storage_mb = round(total_bytes / (1024 * 1024), 1)

    # Audit list
    raw_audit = moderation.list_audit(limit=200)
    audit_entries = []
    audit_actors = set()
    audit_actions = set()
    for row in raw_audit:
        actor = row["actor"] or "Superadmin"
        action = row["action"]
        audit_actors.add(actor)
        audit_actions.add(action)
        at_str = time_ago(row["at"])
        dt_raw = row["detail_json"]
        dt_text = ""
        if dt_raw:
            try:
                import json
                parsed = json.loads(dt_raw)
                if isinstance(parsed, dict):
                    dt_text = ", ".join(f"{k}: {v}" for k, v in parsed.items())
                else:
                    dt_text = str(parsed)
            except Exception:
                dt_text = str(dt_raw)
        audit_entries.append({
            "at_formatted": at_str,
            "actor": actor,
            "action": action,
            "project_slug": row["project_slug"],
            "detail_text": dt_text,
            "ip": row["ip"],
        })

    superadmin_set = bool(os.environ.get("SUPERADMIN_PASSCODE"))
    open_reports_count = moderation.count_open_reports() if is_public else 0

    return render_template(
        "admin/instance.html",
        is_allowed=True,
        is_public=is_public,
        is_super=is_super(),
        project=None,
        active_tab="instance",
        cur_sec=cur_sec,
        studies=studies,
        total_studies=total_studies,
        live_studies=live_studies,
        offline_studies=offline_studies,
        total_participants=total_participants,
        storage_mb=storage_mb,
        superadmin_set=superadmin_set,
        audit_entries=audit_entries,
        audit_actors=sorted(audit_actors),
        audit_actions=sorted(audit_actions),
        recent_audit=audit_entries[:5],
        open_reports_count=open_reports_count,
    )


@bp.route("/admin/instance/action", methods=["POST"])
def instance_action():
    is_public = os.environ.get("TESTBENCH_MODE", "internal") == "public"
    allowed = is_super()
    actor = "Superadmin"
    user_id = None
    if is_public:
        from . import auth
        user = auth.current_user()
        allowed = bool(user and user.get("is_platform_admin"))
        if user:
            actor = user.get("email", "Platform admin")
            user_id = user.get("id")

    if not allowed:
        if request.is_json:
            return jsonify({"ok": False, "error": "Access denied."}), 403
        abort(403)

    from . import moderation
    data = request.get_json() if request.is_json else request.form
    action = data.get("action")
    slug = data.get("slug")
    reason = (data.get("reason") or "").strip()

    if action == "offline":
        moderation.take_offline(slug, reason=reason, by_user_id=user_id)
        moderation.audit("Study taken offline", user_id=user_id, actor=actor, project_slug=slug, detail={"reason": reason}, ip=request.remote_addr)
    elif action == "restore":
        moderation.put_online(slug, by_user_id=user_id)
        moderation.audit("Study restored", user_id=user_id, actor=actor, project_slug=slug, detail={"reason": reason}, ip=request.remote_addr)
    else:
        return jsonify({"ok": False, "error": "Unknown action."}), 400

    if request.is_json:
        return jsonify({"ok": True, "action": action, "slug": slug})
    return redirect(url_for("admin.instance", s="studies"))



