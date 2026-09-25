"""Participant routes: project index, login, home screen, and the generic module flow."""
import hmac
import re
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from . import storage
from .context import Ctx, audience_ok, module_status, participant_id, set_participant
from .i18n import translator
from .modules.base import ADVANCE

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


def passcode_ok(expected, given):
    return bool(expected) and hmac.compare_digest(expected.encode(), (given or "").encode())


@bp.route("/")
def index():
    projects = [p for p in registry().projects.values() if p.listed]
    return render_template("index.html", projects=projects, t=translator("en"))


@bp.route("/health")
def health():
    return {"ok": True, "projects": len(registry().projects)}


# ---------------------------------------------------------------- login and home

@bp.route("/<slug>/", methods=["GET", "POST"])
def home(slug):
    project = get_project(slug)
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
            if not project.passcode:
                errors["passcode"] = t("login.not_configured")
            elif not passcode_ok(project.passcode, request.form.get("passcode")):
                errors["passcode"] = t("login.bad_passcode")
        if project.consent and not request.form.get("consent"):
            errors["consent"] = t("login.need_consent")
        if not errors:
            conn = storage.connect(project.slug)
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
def logout(slug):
    project = get_project(slug)
    set_participant(project.slug, None)
    return redirect(url_for("web.home", slug=slug))


# ---------------------------------------------------------------- module flow

def module_ctx(slug, mid):
    """Loads project, participant, module and session, enforcing login, audience and
    requirements. Returns (ctx, redirect_response)."""
    project = get_project(slug)
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
def module_start(slug, mid):
    ctx, early = module_ctx(slug, mid)
    return early or redirect(ctx.step_url(ctx.session["step"]))


@bp.route("/<slug>/m/<mid>/s/<step>", methods=["GET", "POST"])
def module_step(slug, mid, step):
    ctx, early = module_ctx(slug, mid)
    if early:
        return early
    if step != ctx.session["step"]:
        return redirect(ctx.step_url(ctx.session["step"]))  # the flow is linear
    result = ctx.module.impl.handle(ctx, step)
    return redirect(ctx.advance_url()) if result is ADVANCE else result


@bp.route("/<slug>/m/<mid>/x/<path:path>", methods=["GET", "POST"])
@bp.route("/<slug>/m/<mid>/x/", defaults={"path": ""}, methods=["GET", "POST"])
def module_action(slug, mid, path):
    ctx, early = module_ctx(slug, mid)
    if early:
        abort(409 if request.method == "POST" else 404)
    return ctx.module.impl.action(ctx, path) or abort(404)
