"""Participant routes: project index, login, home screen, and the generic module flow."""
import re
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from . import limits, moderation, passcodes, storage
from .context import Ctx, audience_ok, module_status, participant_id, set_participant
from .i18n import translator
from .modules.base import ADVANCE
from .rate_limit import is_rate_limited, record_failed_login

bp = Blueprint("web", __name__)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
ANON_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, so codes survive being read aloud


def registry():
    return current_app.extensions["testbench"]


def get_project(slug):
    project = registry().get(slug)
    if project is None:
        abort(404)
    return project


def current_participant(project):
    pid = participant_id(project.slug)
    if not pid:
        return None
    conn = storage.connect(project.slug)
    row = conn.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if row is None:
        set_participant(project.slug, None)
    return row


def is_public_mode():
    import os
    return os.environ.get("TESTBENCH_MODE", "internal") == "public"


@bp.route("/")
def index():
    if is_public_mode():
        from . import public
        return public.landing()
    offline = moderation.offline_slugs()
    projects = [p for p in registry().projects.values() if p.listed and p.slug not in offline]
    return render_template("index.html", projects=projects, t=translator("en"))


@bp.route("/health")
def health():
    """Enough for a load balancer to decide, and enough for a human to see why it said no."""
    import shutil
    reg = registry()
    checks = {"projects": len(reg.projects), "config_ok": reg.last_error is None}
    try:
        data_dir = Path(current_app.config["DATA_DIR"])
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".health"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir_writable"] = True
        usage = shutil.disk_usage(data_dir)
        checks["disk_free_mb"] = round(usage.free / (1024 * 1024))
        checks["disk_ok"] = usage.free > 100 * 1024 * 1024
    except OSError as exc:
        checks["data_dir_writable"] = False
        checks["disk_ok"] = False
        checks["error"] = str(exc)
    ok = checks["config_ok"] and checks.get("data_dir_writable") and checks.get("disk_ok")
    return ({"ok": ok, **checks}, 200 if ok else 503)


# ---------------------------------------------------------------- login and home

@bp.route("/<slug>/", methods=["GET", "POST"])
@bp.route("/s/<slug>/", methods=["GET", "POST"])
def home(slug):
    if is_public_mode() and not request.path.startswith("/s/"):
        qs = f"?{request.query_string.decode()}" if request.query_string else ""
        return redirect(f"/s/{slug}/" + qs, code=301)
    project = get_project(slug)
    if moderation.is_offline(project.slug):
        # A study a platform admin took down. Neutral wording on purpose: a participant does not
        # need to know why, and the researcher hears it from us directly.
        return render_template("offline.html", project=project, t=translator(project.locale)), 403
    if project.status == "closed":
        return render_template("closed.html", project=project, t=translator(project.locale))
    if project.status == "draft":
        from .context import is_admin
        if not is_admin(project.slug):
            return render_template("draft.html", project=project, t=translator(project.locale))
    participant = current_participant(project)
    if participant is None:
        return login(project)
    ctx = Ctx(project, participant=participant)
    ctx.conn.execute("UPDATE participants SET last_seen_at = ? WHERE id = ?", (storage.now_iso(), participant["id"]))
    ctx.conn.commit()
    cards = []
    for m in project.modules.values():
        if not audience_ok(ctx, m):
            continue
        status, s = module_status(ctx, m)
        cards.append({"m": m, "status": status, "requires": [project.modules[r].title for r in m.requires]})
    return ctx.render("home.html", cards=cards, just_done=project.module(request.args.get("done", "")),
                      all_done=bool(cards) and all(c["status"] == "done" for c in cards))


def login(project):
    t = translator(project.locale)
    mode = project.identity["mode"]
    errors, form = {}, {"identity": ""}
    if request.method == "POST":
        action = request.form.get("action", "")
        raw = request.form.get("identity", "").strip()
        identity = None
        if mode == "code":
            identity = raw.upper()
            if not re.fullmatch(project.identity["pattern"], identity):
                errors["identity"] = t("login.bad_code")
        elif mode == "email":
            identity = raw.lower()
            domains = project.identity["domains"]
            if not EMAIL_RE.match(identity):
                errors["identity"] = t("login.bad_email")
            elif domains and identity.rsplit("@", 1)[1] not in domains:
                errors["identity"] = t("login.bad_domain", domains=", ".join("@" + d for d in domains))
        else:  # anonymous: start fresh, or resume with the code shown earlier
            if action == "resume":
                identity = raw.upper()
                exists = storage.connect(project.slug).execute(
                    "SELECT 1 FROM participants WHERE identity = ?", (identity,)).fetchone()
                if not exists:
                    errors["identity"] = t("login.unknown_code")
        form["identity"] = raw
        if project.access == "passcode":
            if not passcodes.source(project, "participant"):
                errors["passcode"] = t("login.not_configured")
            elif is_rate_limited(request.remote_addr, f"participant:{project.slug}"):
                errors["passcode"] = "Too many failed attempts. Try again later."
            elif not passcodes.check(project, "participant", request.form.get("passcode")):
                record_failed_login(request.remote_addr, f"participant:{project.slug}")
                errors["passcode"] = t("login.bad_passcode")
        if project.consent and not request.form.get("consent"):
            errors["consent"] = t("login.need_consent")
        if not errors:
            conn = storage.connect(project.slug)
            known = identity and conn.execute(
                "SELECT 1 FROM participants WHERE identity = ?", (identity,)).fetchone()
            if not known and limits.project_full(project.slug):
                # The study has all the participants it is allowed. Whoever already started can
                # still finish, so nobody loses a half-done session.
                return render_template("full.html", project=project, t=t), 403
            if mode == "anonymous" and action != "resume":
                identity = new_anonymous_code(conn)
            row = conn.execute("SELECT id FROM participants WHERE identity = ?", (identity,)).fetchone()
            if row is None:
                ts = storage.now_iso()
                pid = conn.execute("INSERT INTO participants (identity, created_at, last_seen_at) VALUES (?, ?, ?)",
                                   (identity, ts, ts)).lastrowid
                conn.commit()
            else:
                pid = row["id"]
            set_participant(project.slug, pid)
            return redirect(url_for("web.home", slug=project.slug))
    return render_template("login.html", project=project, t=t, errors=errors, form=form, mode=mode)


def new_anonymous_code(conn):
    while True:
        code = "".join(secrets.choice(ANON_ALPHABET) for _ in range(8))
        code = f"{code[:4]}-{code[4:]}"
        if not conn.execute("SELECT 1 FROM participants WHERE identity = ?", (code,)).fetchone():
            return code


@bp.route("/<slug>/logout")
@bp.route("/s/<slug>/logout")
def logout(slug):
    if is_public_mode() and not request.path.startswith("/s/"):
        return redirect(f"/s/{slug}/logout", code=301)
    project = get_project(slug)
    set_participant(project.slug, None)
    return redirect(url_for("web.home", slug=slug))


# ---------------------------------------------------------------- module flow

def module_ctx(slug, mid):
    """Loads project, participant, module and session, enforcing login, audience and
    requirements. Returns (ctx, redirect_response)."""
    project = get_project(slug)
    if moderation.is_offline(project.slug) or project.status == "closed":
        return None, redirect(url_for("web.home", slug=slug))
    if project.status == "draft":
        from .context import is_admin
        if not is_admin(project.slug):
            return None, redirect(url_for("web.home", slug=slug))
    participant = current_participant(project)
    if participant is None:
        return None, redirect(url_for("web.home", slug=slug))
    module = project.module(mid)
    if module is None:
        abort(404)
    ctx = Ctx(project, module=module, participant=participant)
    if not audience_ok(ctx, module):
        abort(404)
    status, s = module_status(ctx, module)
    if status in ("locked", "done"):
        return None, redirect(ctx.home_url())
    if s is None:
        state = module.impl.on_start(ctx)
        first = module.impl.steps(state)[0]
        ctx.conn.execute("INSERT INTO sessions (participant_id, module_id, step, state, started_at) VALUES (?, ?, ?, ?, ?)",
                         (participant["id"], module.id, first, storage.dumps(state), storage.now_iso()))
        ctx.conn.commit()
        s = storage.session_for(ctx.conn, participant["id"], module.id)
    return Ctx(project, module=module, participant=participant, session=s), None


@bp.route("/<slug>/m/<mid>/")
@bp.route("/s/<slug>/m/<mid>/")
def module_start(slug, mid):
    if is_public_mode() and not request.path.startswith("/s/"):
        return redirect(f"/s/{slug}/m/{mid}/", code=301)
    ctx, early = module_ctx(slug, mid)
    return early or redirect(ctx.step_url(ctx.session["step"]))


@bp.route("/<slug>/m/<mid>/s/<step>", methods=["GET", "POST"])
@bp.route("/s/<slug>/m/<mid>/s/<step>", methods=["GET", "POST"])
def module_step(slug, mid, step):
    if is_public_mode() and not request.path.startswith("/s/"):
        return redirect(f"/s/{slug}/m/{mid}/s/{step}", code=301)
    ctx, early = module_ctx(slug, mid)
    if early:
        return early
    if step != ctx.session["step"]:
        return redirect(ctx.step_url(ctx.session["step"]))  # the flow is linear
    result = ctx.module.impl.handle(ctx, step)
    return redirect(ctx.advance_url()) if result is ADVANCE else result


@bp.route("/<slug>/m/<mid>/x/<path:path>", methods=["GET", "POST"])
@bp.route("/<slug>/m/<mid>/x/", defaults={"path": ""}, methods=["GET", "POST"])
@bp.route("/s/<slug>/m/<mid>/x/<path:path>", methods=["GET", "POST"])
@bp.route("/s/<slug>/m/<mid>/x/", defaults={"path": ""}, methods=["GET", "POST"])
def module_action(slug, mid, path):
    if is_public_mode() and not request.path.startswith("/s/"):
        return redirect(f"/s/{slug}/m/{mid}/x/{path}", code=301)
    ctx, early = module_ctx(slug, mid)
    if early:
        abort(409 if request.method == "POST" else 404)
    return ctx.module.impl.action(ctx, path) or abort(404)
