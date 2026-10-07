"""Public routes: landing page, researcher dashboard (/app/), legal pages, and docs."""
import os
import re
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, session as cookie, url_for

from . import auth, limits, mail, markdown, moderation, storage, users
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

DOCS_DIR = Path(__file__).parent.parent / "docs"
# Only these pages are served at /docs/. The rest of docs/ (plans, decision records, the
# operations runbook) is for people working on or running the code, and stays in the repo.
PUBLIC_DOCS = ("configuration", "statistics")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def doc_path(page):
    """Resolve a /docs/<page> path to a file inside docs/, or None.

    Pages live in subdirectories (`plans/saas-readiness`, `decisions/001-...`), so the path can
    carry slashes. Each segment is checked against a strict pattern and the result is resolved
    and compared against the docs directory, so neither `..` nor a symlink can escape it.
    """
    segments = [seg for seg in page.split("/") if seg]
    if "/".join(segments) not in PUBLIC_DOCS:
        return None
    if not segments or not all(SEGMENT_RE.match(seg) for seg in segments):
        return None
    candidate = (DOCS_DIR / "/".join(segments)).with_suffix(".md")
    try:
        resolved = candidate.resolve()
        resolved.relative_to(DOCS_DIR.resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def doc_index():
    """Every doc, grouped by folder, for the sidebar. Top-level pages come first."""
    groups = {}
    for path in sorted(DOCS_DIR.rglob("*.md")):
        rel = path.relative_to(DOCS_DIR)
        if rel.with_suffix("").as_posix() not in PUBLIC_DOCS:
            continue
        group = rel.parent.as_posix() if rel.parent.as_posix() != "." else ""
        slug = rel.with_suffix("").as_posix()
        title = rel.stem.replace("-", " ").replace("_", " ")
        title = re.sub(r"^\d+\s+", "", title)          # decision records are numbered
        groups.setdefault(group, []).append({"slug": slug, "title": title[:1].upper() + title[1:]})
    return dict(sorted(groups.items(), key=lambda kv: (kv[0] != "", kv[0])))


@bp.route("/docs/")
@bp.route("/docs/<path:page>")
def docs(page="configuration"):
    path = doc_path(page)
    if path is None:
        abort(404)
    content, headings = markdown.render(path.read_text(encoding="utf-8"))
    title = headings[0][1] if headings and headings[0][0] == 1 else path.stem.replace("-", " ")
    return render_template(
        "public/doc.html", content=content, page=page.strip("/"), title=title,
        # Only h2/h3 make a useful contents list; h1 is the page title.
        toc=[h for h in headings if h[0] in (2, 3)],
        groups=doc_index(), t=translator("en"), user=auth.current_user())


# ---------------------------------------------------------------- researcher dashboard

@bp.route("/app/")
def dashboard():
    if os.environ.get("TESTBENCH_MODE", "internal") != "public":
        return redirect(url_for("admin.overview"))
    user = auth.current_user()
    if not user:
        return redirect(url_for("auth.login", next=request.path))

    memberships = {m["project_slug"]: m["role"] for m in users.user_projects(user["id"])}
    reg = registry()
    from . import admin as admin_mod
    studies = []
    slugs_seen = set()

    for slug, role in memberships.items():
        slugs_seen.add(slug)
        p = reg.get(slug)
        if p:
            studies.append(admin_mod.get_study_card(p, role=role))

    if user["is_platform_admin"]:
        for p in reg.projects.values():
            if p.slug not in slugs_seen:
                studies.append(admin_mod.get_study_card(p, role="admin"))

    total_participants = sum(s["participants"] for s in studies)
    counts = {
        "all": len(studies),
        "draft": sum(1 for s in studies if s["status"] == "draft"),
        "live": sum(1 for s in studies if s["status"] == "live"),
        "closed": sum(1 for s in studies if s["status"] == "closed")
    }

    return render_template("admin/overview.html", user=user, studies=studies,
                           total_participants=total_participants, counts=counts, max_studies=10,
                           is_super=lambda: bool(user["is_platform_admin"]), t=translator("en"))


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
        duplicate_from = (request.args.get("from") or "").strip() or None
        if duplicate_from and duplicate_from != "example":
            if registry().get(duplicate_from) is None:
                flash(f"Project '{duplicate_from}' not found.", "error")
                return redirect(url_for("public.dashboard"))
            if not users.can(user, duplicate_from, "view"):
                flash(f"You do not have permission to copy project '{duplicate_from}'.", "error")
                return redirect(url_for("public.dashboard"))
        taken = [p.slug for p in registry().projects.values()]
        return render_template("admin/new_study.html", user=user, locales=available(), taken_slugs=taken, t=translator("en"))

    slug = request.form.get("slug", "").strip().lower()
    name = request.form.get("name", "").strip() or slug
    locale = request.form.get("locale", "en")
    source = request.form.get("source", "ab")
    mode = request.form.get("identity", "code")
    access = "open" if request.form.get("access") == "open" else "passcode"
    brand = request.form.get("brand", "#2563EB")
    pattern = request.form.get("pattern", "^P\\d{2}$")
    domains = request.form.get("domains", "")
    upload = request.files.get("archive")
    duplicate_from = (request.args.get("from") or request.form.get("from") or "").strip() or None

    if duplicate_from and duplicate_from != "example":
        if registry().get(duplicate_from) is None:
            flash(f"Source project '{duplicate_from}' not found.", "error")
            return redirect(url_for("public.new_project"))
        if not users.can(user, duplicate_from, "view"):
            flash(f"You do not have permission to copy project '{duplicate_from}'.", "error")
            return redirect(url_for("public.new_project"))

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
            scaffold_errs = studio.build_project_scaffold(
                tmp_dst, slug, name, source, locale,
                mode if mode in ("code", "email", "anonymous") else "code",
                access, brand=brand, pattern=pattern, domains=domains,
                upload=upload, duplicate_from=duplicate_from
            )
            if scaffold_errs:
                flash("; ".join(scaffold_errs[:3]), "error")
                return redirect(url_for("public.new_project"))

            problems = studio.install(tmp_dst, slug)
            if problems:
                flash("; ".join(problems[:3]), "error")
                return redirect(url_for("public.new_project"))

        users.add_membership(slug, user["id"], "owner")
        moderation.audit("project.create", user_id=user["id"], actor=user["email"],
                         project_slug=slug, detail={"source": source}, ip=request.remote_addr)
        registry().refresh(force=True)
        return redirect(url_for("admin.dashboard", slug=slug, created=1))
    except Exception as exc:
        flash(f"Could not create project: {exc}", "error")
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
                    base = auth._base_url()
                    if mail.configured():
                        mail.send_invitation(email, token, base,
                                             project.name, user["name"] or user["email"], role)
                    moderation.audit("member.invite", user_id=user["id"], actor=user["email"],
                                     project_slug=slug, detail={"email": email, "role": role},
                                     ip=request.remote_addr)
                    # Only the hash is stored, so this is the one chance to show the link. Sharing it
                    # by hand works with or without SMTP: it is only good for this address.
                    cookie["invite_link"] = {"email": email,
                                             "url": base + url_for("public.accept_invite", token=token),
                                             "emailed": mail.configured()}
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
                           invite_link=cookie.pop("invite_link", None),
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
    cookie.pop("pending_invite", None)
    moderation.audit("member.accept", user_id=user["id"], actor=user["email"], project_slug=slug,
                     detail={"role": invite["role"]}, ip=request.remote_addr)
    flash(f"You have joined {slug} as {invite['role']}.", "success")
    return redirect(url_for("public.dashboard"))
