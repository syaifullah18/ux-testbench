"""Public routes: landing page, researcher dashboard (/app/), legal pages, and docs."""
import os
import re
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from . import auth, limits, mail, moderation, storage, users
from .config import RESERVED_SLUGS, SLUG_RE
from .i18n import translator
from .web import get_project, registry

bp = Blueprint("public", __name__)


def _project_stats(slug):
    try:
        conn = storage.connect(slug)
        n = conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
        last = conn.execute("SELECT MAX(last_seen_at) FROM participants").fetchone()[0]
        return n, last
    except Exception:
        return 0, None


def landing():
    return render_template("public/landing.html", t=translator("en"),
                           has_example=registry().get("example") is not None, user=auth.current_user())


@bp.route("/about")
def about():
    return render_template("public/about.html", t=translator("en"), user=auth.current_user())

@bp.route("/privacy")
def privacy():
    return render_template("public/privacy.html", t=translator("en"), user=auth.current_user())

@bp.route("/terms")
def terms():
    return render_template("public/terms.html", t=translator("en"), user=auth.current_user())

@bp.route("/explore")
def explore():
    """Studies a platform admin has approved. Being `listed` is the researcher's opt-in; the
    approval is ours, so nobody can put a study in front of the public unreviewed."""
    approved = moderation.explore_approved_slugs()
    studies = [p for p in registry().projects.values()
               if p.listed and p.status != "draft" and p.slug in approved]
    return render_template("public/explore.html", studies=studies, t=translator("en"), user=auth.current_user())


@bp.route("/report", methods=["GET", "POST"])
def report():
    sent = False
    if request.method == "POST":
        slug = request.form.get("slug", "").strip()
        reason = request.form.get("reason", "").strip()
        contact = request.form.get("contact", "").strip()
        if reason:
            slug = slug.rsplit("/", 1)[-1].strip().lower() if "/" in slug else slug.lower()
            report_id = moderation.add_report(slug, reason, contact, ip=request.remote_addr)
            moderation.audit("report.filed", actor=contact or "anonymous", project_slug=slug,
                             detail={"report": report_id}, ip=request.remote_addr)
        sent = True
    return render_template("public/report.html", sent=sent, t=translator("en"), user=auth.current_user())


# ---------------------------------------------------------------- docs reader

def render_simple_markdown(text):
    """Safe, zero-dependency subset markdown to HTML converter."""
    html_lines = []
    in_code = False
    code_buf = []

    for line in text.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append(f"<pre class='bg-slate-900 text-slate-100 p-4 rounded-xl text-xs overflow-x-auto my-4'><code>{escape(''.join(code_buf))}</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(line + "\n")
            continue

        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            level = min(len(s) - len(s.lstrip("#")), 6)
            txt = escape(s.lstrip("#").strip())
            sizes = {1: "text-2xl font-bold mt-6 mb-3", 2: "text-xl font-bold mt-5 mb-2.5", 3: "text-lg font-semibold mt-4 mb-2"}
            html_lines.append(f"<h{level} class='{sizes.get(level, 'text-base font-semibold')} text-slate-900'>{txt}</h{level}>")
        elif s.startswith(("- ", "* ")):
            content = re.sub(r"`([^`]+)`", r"<code class='bg-slate-100 px-1 py-0.5 rounded text-xs'>\1</code>",
                             re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escape(s[2:])))
            html_lines.append(f"<li class='ml-5 list-disc text-slate-600 text-sm my-1'>{content}</li>")
        else:
            content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href='\2' class='text-brand underline'>\1</a>",
                             re.sub(r"`([^`]+)`", r"<code class='bg-slate-100 px-1 py-0.5 rounded text-xs'>\1</code>",
                                    re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escape(line))))
            html_lines.append(f"<p class='text-slate-600 text-sm leading-relaxed my-2'>{content}</p>")

    if in_code and code_buf:
        html_lines.append(f"<pre class='bg-slate-900 text-slate-100 p-4 rounded-xl text-xs overflow-x-auto my-4'><code>{escape(''.join(code_buf))}</code></pre>")

    return Markup("".join(html_lines))


@bp.route("/docs/")
@bp.route("/docs/<page>")
def docs(page="configuration"):
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "", page)
    doc_path = Path(__file__).parent.parent / "docs" / f"{safe_name}.md"
    if not doc_path.exists() or not doc_path.is_file():
        abort(404)

    content = render_simple_markdown(doc_path.read_text(encoding="utf-8"))
    doc_files = [p.stem for p in (Path(__file__).parent.parent / "docs").glob("*.md")]
    return render_template("public/doc.html", content=content, page=safe_name, doc_files=doc_files,
                           t=translator("en"), user=auth.current_user())


# ---------------------------------------------------------------- researcher dashboard

@bp.route("/app/")
def dashboard():
    if os.environ.get("TESTBENCH_MODE", "internal") != "public":
        return redirect(url_for("admin.overview"))
    user = auth.current_user()
    if not user:
        return redirect(url_for("auth.login", next=request.path))

    memberships = users.user_projects(user["id"])
    reg = registry()
    cards = []
    total_participants = 0
    slugs_seen = set()

    for m in memberships:
        slug = m["project_slug"]
        slugs_seen.add(slug)
        p = reg.get(slug)
        if p:
            n_parts, last_seen = _project_stats(slug)
            total_participants += n_parts
            cards.append({"project": p, "role": m["role"], "participants": n_parts, "last_seen": last_seen})

    if user["is_platform_admin"]:
        for p in reg.projects.values():
            if p.slug not in slugs_seen:
                n_parts, last_seen = _project_stats(p.slug)
                cards.append({"project": p, "role": "admin", "participants": n_parts, "last_seen": last_seen})

    return render_template("public/dashboard.html", user=user, projects=cards,
                           total_participants=total_participants, t=translator("en"))


@bp.route("/app/new", methods=["GET", "POST"])
def new_project():
    if os.environ.get("TESTBENCH_MODE", "internal") != "public":
        abort(404)
    user = auth.current_user()
    if not user:
        return redirect(url_for("auth.login"))

    blocked = limits.check_new_project(user)
    if blocked:
        flash(blocked, "error")
        return redirect(url_for("public.dashboard"))

    from .i18n import available
    if request.method == "GET":
        return render_template("public/new_project.html", user=user, locales=available(), t=translator("en"))

    slug = request.form.get("slug", "").strip().lower()
    name = request.form.get("name", "").strip() or slug
    locale = request.form.get("locale", "en")
    source = request.form.get("source", "blank")

    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        flash("Slug must be 1-40 lowercase letters, digits and hyphens, and not reserved.", "error")
        return redirect(url_for("public.new_project"))
    if registry().get(slug) is not None:
        flash(f"Project slug '{slug}' is already taken.", "error")
        return redirect(url_for("public.new_project"))

    from . import studio
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dst = Path(tmp) / slug
            if source == "import":
                upload = request.files.get("archive")
                if not upload or not upload.filename:
                    flash("Please select a valid ZIP archive.", "error")
                    return redirect(url_for("public.new_project"))
                tmp_dst.mkdir()
                studio.extract_zip(upload.stream, tmp_dst, studio.PROJECT_EXT, strip_single_root=True)
            elif source == "example":
                example_p = registry().get("example")
                if example_p:
                    shutil.copytree(example_p.dir, tmp_dst, ignore=shutil.ignore_patterns(".history", "*.db*"))
                    data = studio.read_project_yaml(tmp_dst)
                    data["name"] = name
                    data["status"] = "draft"
                    data["listed"] = False
                    data["access"] = "passcode"
                    (tmp_dst / "project.yaml").write_text(studio.dump_yaml(data), encoding="utf-8")
                else:
                    source = "blank"
            if source == "blank":
                (tmp_dst / "modules").mkdir(parents=True)
                yaml_txt = f"name: {name}\nstatus: draft\nlisted: false\nlocale: {locale}\naccess: passcode\nidentity:\n  mode: code\nmodules:\n  - feedback\n"
                (tmp_dst / "project.yaml").write_text(yaml_txt, encoding="utf-8")
                shutil.copy(studio.SCAFFOLD / "modules" / "feedback.yaml", tmp_dst / "modules" / "feedback.yaml")

            problems = studio.install(tmp_dst, slug)
            if problems:
                flash("; ".join(problems[:3]), "error")
                return redirect(url_for("public.new_project"))

        users.add_membership(slug, user["id"], "owner")
        moderation.audit("project.create", user_id=user["id"], actor=user["email"],
                         project_slug=slug, detail={"source": source}, ip=request.remote_addr)
        registry().refresh(force=True)
        flash(f"Project '{name}' created successfully!", "success")
        return redirect(url_for("studio.home", slug=slug))
    except Exception as exc:
        flash(f"Creation failed: {exc}", "error")
        return redirect(url_for("public.new_project"))


@bp.post("/app/p/<slug>/status")
def update_status(slug):
    user = auth.current_user()
    if not user:
        return redirect(url_for("auth.login"))
    if not users.can(user, slug, "edit"):
        abort(403)
    new_status = request.form.get("status", "").lower()
    if new_status not in ("draft", "live", "closed"):
        abort(400)
    project = get_project(slug)
    if not project.editable:
        flash("This project is read-only.", "error")
        return redirect(url_for("public.dashboard"))

    from . import studio
    data = studio.read_project_yaml(project.dir)
    data["status"] = new_status
    (project.dir / "project.yaml").write_text(studio.dump_yaml(data), encoding="utf-8")
    moderation.audit("project.status", user_id=user["id"], actor=user["email"], project_slug=slug,
                     detail={"status": new_status}, ip=request.remote_addr)
    registry().refresh(force=True)
    flash(f"Status changed to {new_status}.", "success")
    return redirect(request.referrer or url_for("public.dashboard"))


# ---------------------------------------------------------------- team: invite and accept

@bp.route("/app/p/<slug>/members", methods=["GET", "POST"])
def members(slug):
    """Who may work on a study. Inviting a teammate by email replaces the old habit of passing a
    shared admin passcode around: one person can be removed without changing anything for the
    rest."""
    if not auth.is_public_mode():
        abort(404)
    user = auth.current_user()
    if not user:
        return redirect(url_for("auth.login", next=request.path))
    if not users.can(user, slug, "manage"):
        abort(403)
    project = get_project(slug)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "invite":
            email = request.form.get("email", "").strip().lower()
            role = request.form.get("role", "viewer")
            if "@" not in email:
                flash("That does not look like an email address.", "error")
            elif role not in users.ROLES:
                flash("Pick a role from the list.", "error")
            else:
                existing = users.get_user_by_email(email)
                if existing and users.get_membership(slug, existing["id"]):
                    flash(f"{email} is already a member.", "error")
                else:
                    _, token = users.create_invitation(slug, email, role, user["id"])
                    mail.send_invitation(email, token, request.host_url.rstrip("/"),
                                         project.name, user["name"] or user["email"], role)
                    moderation.audit("member.invite", user_id=user["id"], actor=user["email"],
                                     project_slug=slug, detail={"email": email, "role": role},
                                     ip=request.remote_addr)
                    flash(f"Invitation sent to {email}.", "success")
        elif action == "revoke_invite":
            users.revoke_invitation(slug, int(request.form.get("invitation_id", 0)))
            flash("Invitation revoked.", "success")
        elif action == "set_role":
            target_id = int(request.form.get("user_id", 0))
            role = request.form.get("role", "")
            if role not in users.ROLES:
                abort(400)
            if target_id == user["id"] and role != "owner":
                flash("Ask another owner to change your own role, so a study is never left ownerless.", "error")
            else:
                users.add_membership(slug, target_id, role)
                moderation.audit("member.role", user_id=user["id"], actor=user["email"],
                                 project_slug=slug, detail={"user": target_id, "role": role},
                                 ip=request.remote_addr)
                flash("Role updated.", "success")
        elif action == "remove":
            target_id = int(request.form.get("user_id", 0))
            owners = [m for m in users.project_members(slug) if m["role"] == "owner"]
            if len(owners) == 1 and owners[0]["user_id"] == target_id:
                flash("This is the only owner. Make someone else an owner first.", "error")
            else:
                users.remove_membership(slug, target_id)
                moderation.audit("member.remove", user_id=user["id"], actor=user["email"],
                                 project_slug=slug, detail={"user": target_id},
                                 ip=request.remote_addr)
                flash("Member removed.", "success")
        return redirect(url_for("public.members", slug=slug))

    return render_template("public/members.html", user=user, project=project,
                           members=users.project_members(slug),
                           invitations=users.pending_invitations(slug),
                           roles=users.ROLES, t=translator("en"))


@bp.route("/invite/<token>")
def accept_invite(token):
    """The link from an invitation email. Someone with an account joins straight away; someone
    without one is sent to sign-up with the invitation held for them."""
    if not auth.is_public_mode():
        abort(404)
    t = translator("en")
    invite = users.get_invitation(token)
    if invite is None:
        return render_template("auth/message.html", t=t, project=None,
                               title="This invitation is no longer valid",
                               message=("It may have expired, been used already, or been revoked. "
                                        "Ask whoever invited you to send a new one.")), 410

    user = auth.current_user()
    if user is None:
        from flask import session as cookie
        cookie["pending_invite"] = token
        if users.get_user_by_email(invite["email"]):
            flash("Sign in to accept the invitation.", "success")
            return redirect(url_for("auth.login", next=request.path))
        return redirect(url_for("auth.signup", invite=token))

    if user["email"].lower() != invite["email"].lower():
        return render_template("auth/message.html", t=t, project=None,
                               title="This invitation is for another address",
                               message=(f"It was sent to {invite['email']}, but you are signed in as "
                                        f"{user['email']}. Sign out and sign in with the invited "
                                        "address, or ask for an invitation to this one.")), 403

    slug = users.accept_invitation(token, user["id"])
    from flask import session as cookie
    cookie.pop("pending_invite", None)
    moderation.audit("member.accept", user_id=user["id"], actor=user["email"], project_slug=slug,
                     detail={"role": invite["role"]}, ip=request.remote_addr)
    flash(f"You have joined {slug} as {invite['role']}.", "success")
    return redirect(url_for("public.dashboard"))
