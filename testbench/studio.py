"""Studio: manage projects from the admin site instead of the file system.

Every change is validated against a scratch copy of the project first and only written when
the whole project still loads, so a typo can never take a live study down. Overwritten and
deleted YAML files are kept under `<project>/.history/`.

Who can do what:
- a project's admin edits that project (settings, modules, prototypes, passcodes, export);
- the superadmin also creates, imports, duplicates and deletes projects.
Only projects inside the Studio folder (DATA_DIR/projects) are editable; projects from
PROJECTS_DIRS are read-only here and can be duplicated into the Studio.
"""
import io
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request, send_file,
                   send_from_directory, session as cookie, url_for)

from . import limits, passcodes, storage
from .config import SLUG_RE, ID_RE, RESERVED_SLUGS, Problems, env_prefix, load_project
from .context import Ctx, is_admin, is_super, can_edit
from .i18n import available as available_locales, translator
from .modules import MODULE_TYPES
from .web import get_project, registry

bp = Blueprint("studio", __name__)

SCAFFOLD = Path(__file__).parent / "scaffold"
PROTOTYPE_EXT = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".map", ".txt", ".csv", ".xml",
                 ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico",
                 ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".mp3", ".pdf"}
PROJECT_EXT = PROTOTYPE_EXT | {".yaml", ".yml", ".md"}
ZIP_MAX_FILES = 2000
ZIP_MAX_BYTES = 200 * 1024 * 1024
HISTORY_KEEP = 30
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------- helpers


def studio_project(slug, write=True):
    project = get_project(slug)
    if write:
        if not can_edit(slug):
            abort(403)
        if not project.editable:
            abort(403)
    else:
        if not is_admin(slug):
            abort(403)
    return project


def safe_parts(name):
    """Splits an uploaded path into safe segments, or returns None. Rejects absolute paths,
    '..', hidden files and anything outside a conservative character set."""
    parts = [p for p in re.split(r"[\\/]+", name or "") if p not in ("", ".")]
    if not parts or any(p == ".." or not SEGMENT_RE.match(p.replace(" ", "-")) for p in parts):
        return None
    return [p.replace(" ", "-") for p in parts]


def inside(base, rel):
    target = (base / rel).resolve()
    base = base.resolve()
    if target != base and base not in target.parents:
        abort(400)
    return target


def trial(project_dir, slug, writes=None, deletes=()):
    """Loads a scratch copy of the project with the given changes and returns its problems.
    Problem paths are made relative to the project so they read well in the editor."""
    problems = Problems()
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / slug
        shutil.copytree(project_dir, dst, ignore=shutil.ignore_patterns(".history"))
        for rel, text in (writes or {}).items():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / rel).write_text(text, encoding="utf-8")
        for rel in deletes:
            (dst / rel).unlink(missing_ok=True)
        load_project(dst, MODULE_TYPES, problems)
        return [p.replace(str(Path(tmp)) + "/", "") for p in problems]


def backup(project_dir, rel):
    src = project_dir / rel
    if not src.is_file():
        return
    hist = project_dir / ".history"
    hist.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    shutil.copy2(src, hist / f"{rel.replace('/', '__')}.{stamp}.bak")
    for old in sorted(hist.glob(f"{rel.replace('/', '__')}.*.bak"))[:-HISTORY_KEEP]:
        old.unlink()


def write_text(project_dir, rel, text):
    backup(project_dir, rel)
    target = project_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)


def history(project_dir, rel=None):
    hist = project_dir / ".history"
    if not hist.is_dir():
        return []
    pattern = f"{rel.replace('/', '__')}.*.bak" if rel else "*.bak"
    items = []
    for p in sorted(hist.glob(pattern), reverse=True):
        name, stamp = p.name[:-4].rsplit(".", 1)
        items.append({"file": name.replace("__", "/"), "stamp": stamp, "id": p.name,
                      "when": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]} UTC"})
    return items


def reload():
    registry().refresh(force=True)


def editable_files(project_dir):
    files = ["project.yaml"] + sorted(f"modules/{p.name}" for p in (project_dir / "modules").glob("*.yaml"))
    return files


def check_editable_rel(rel):
    if rel != "project.yaml" and not re.fullmatch(r"modules/[a-z0-9][a-z0-9_-]{0,39}\.yaml", rel or ""):
        abort(400)


def read_project_yaml(project_dir):
    return yaml.safe_load((project_dir / "project.yaml").read_text(encoding="utf-8")) or {}


def dump_yaml(data):
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def slug_taken(slug):
    return registry().get(slug) is not None or (registry().studio / slug).exists()


def validate_new_slug(slug, t):
    if not SLUG_RE.match(slug or "") or slug in RESERVED_SLUGS:
        return t("studio.bad_slug")
    if slug_taken(slug):
        return t("studio.slug_taken", slug=slug)
    return None


def install(src_dir, slug):
    """Validates a complete project folder and moves it into the Studio."""
    problems = Problems()
    load_project(src_dir, MODULE_TYPES, problems)
    if problems:
        return [p.replace(str(src_dir.parent) + "/", "") for p in problems]
    shutil.copytree(src_dir, registry().studio / slug, ignore=shutil.ignore_patterns(".history"))
    reload()
    return []


def project_yaml_text(name, locale, mode, access, description="", brand="#2563EB", modules=None, pattern=None, domains=None):
    import json
    if mode == "code":
        pat = pattern or "^P\\d{2}$"
        identity = f"  mode: code\n  pattern: {json.dumps(pat)}      # codes you hand out, e.g. P01\n"
    elif mode == "email":
        dom_list = f" [{domains}]" if domains else " [example.com]"
        identity = f"  mode: email\n  # domains:{dom_list}\n"
    else:
        identity = "  mode: anonymous          # the app issues a resume code\n"

    mod_lines = "\n".join(f"  - {m}" for m in (modules or ["feedback"]))
    return (f"name: {yaml.safe_dump(name, allow_unicode=True).strip().removesuffix('...').strip()}\n"
            f"description: {yaml.safe_dump(description or 'Describe the study for participants.', allow_unicode=True).strip().removesuffix('...').strip()}\n"
            f"locale: {locale}\nlisted: false\n\nidentity:\n{identity}\n"
            f"access: {access}               # passcode | open; set passcodes in the Studio\n\n"
            "consent: >-\n  I agree that my answers are recorded for this research.\n\n"
            f'brand:\n  primary: "{brand}"\n  nav: "#0F172A"\n\n'
            f"modules:\n{mod_lines}\n")


def build_project_scaffold(dst, slug, name, source, locale, mode, access, brand="#2563EB", pattern=None, domains=None, upload=None, duplicate_from=None):
    (dst / "modules").mkdir(parents=True, exist_ok=True)
    if source == "import":
        if not upload or not upload.filename:
            return ["No import file provided"]
        names = extract_zip(upload.stream, dst, PROJECT_EXT, strip_single_root=True)
        if "project.yaml" not in names:
            return ["Archive has no project.yaml"]
        data = read_project_yaml(dst)
        data["name"] = name or data.get("name", slug)
        if brand:
            data.setdefault("brand", {})["primary"] = brand
        (dst / "project.yaml").write_text(dump_yaml(data), encoding="utf-8")
        return []
    elif source in ("example", "duplicate"):
        src_slug = duplicate_from or "example"
        src = registry().get(src_slug)
        if src is None:
            if registry().projects:
                src = next(iter(registry().projects.values()))
            else:
                return [f"Source project '{src_slug}' not found"]
        shutil.copytree(src.dir, dst, ignore=shutil.ignore_patterns(".history"), dirs_exist_ok=True)
        data = read_project_yaml(dst)
        data["name"] = name or f"{data.get('name', src_slug)} (copy)"
        data.pop("passcode_env", None)
        data.pop("admin_passcode_env", None)
        if brand:
            data.setdefault("brand", {})["primary"] = brand
        (dst / "project.yaml").write_text(dump_yaml(data), encoding="utf-8")
        return []
    elif source == "ab":
        modules = ["events-ab", "feedback"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "templates" / "ab_test.yaml", dst / "modules" / "events-ab.yaml")
        shutil.copy(SCAFFOLD / "modules" / "feedback.yaml", dst / "modules" / "feedback.yaml")
        proto = dst / "prototypes"
        proto.mkdir(parents=True, exist_ok=True)
        shutil.copy(SCAFFOLD / "templates" / "variant-a.html", proto / "variant-a.html")
        shutil.copy(SCAFFOLD / "templates" / "variant-b.html", proto / "variant-b.html")
        return []
    elif source == "survey":
        modules = ["survey"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "templates" / "survey.yaml", dst / "modules" / "survey.yaml")
        return []
    elif source == "tree":
        modules = ["info-arch"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "templates" / "tree_test.yaml", dst / "modules" / "info-arch.yaml")
        return []
    elif source == "sort":
        modules = ["card-sort"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "templates" / "card_sort.yaml", dst / "modules" / "card-sort.yaml")
        return []
    elif source == "click":
        modules = ["first-click"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "templates" / "first_click.yaml", dst / "modules" / "first-click.yaml")
        return []
    else:  # blank
        modules = ["feedback"]
        yaml_content = project_yaml_text(name, locale, mode, access, brand=brand, modules=modules, pattern=pattern, domains=domains)
        (dst / "project.yaml").write_text(yaml_content, encoding="utf-8")
        shutil.copy(SCAFFOLD / "modules" / "feedback.yaml", dst / "modules" / "feedback.yaml")
        return []


def studio_ctx(project):
    return Ctx(project, admin=True)


# ---------------------------------------------------------------- superadmin: create, import

@bp.route("/admin/projects/new", methods=["GET", "POST"])
def new_project():
    if not is_super():
        abort(403)
    if request.method == "GET":
        taken = [p.slug for p in registry().projects.values()]
        return render_template("admin/new_study.html", locales=available_locales(), taken_slugs=taken)
    t = translator("en")
    slug = request.form.get("slug", "").strip().lower()
    err = validate_new_slug(slug, t)
    source = request.form.get("source", "ab")
    name = request.form.get("name", "").strip() or slug
    locale = request.form.get("locale") if request.form.get("locale") in available_locales() else "en"
    mode = request.form.get("identity", "code")
    access = "open" if request.form.get("access") == "open" else "passcode"
    brand = request.form.get("brand", "#2563EB")
    pattern = request.form.get("pattern", "^P\\d{2}$")
    domains = request.form.get("domains", "")
    upload = request.files.get("archive")
    duplicate_from = request.args.get("from") or request.form.get("from")

    if err:
        return redirect(url_for("admin.overview", error=err))
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / slug
        scaffold_errs = build_project_scaffold(
            dst, slug, name, source, locale,
            mode if mode in ("code", "email", "anonymous") else "code",
            access, brand=brand, pattern=pattern, domains=domains,
            upload=upload, duplicate_from=duplicate_from
        )
        if scaffold_errs:
            return redirect(url_for("admin.overview", error="; ".join(scaffold_errs[:3])))
        problems = install(dst, slug)
    if problems:
        return redirect(url_for("admin.overview", error="; ".join(problems[:3])))
    return redirect(url_for("studio.home", slug=slug, created=1))


@bp.post("/admin/projects/import")
def import_project():
    if not is_super():
        abort(403)
    t = translator("en")
    upload = request.files.get("archive")
    slug = request.form.get("slug", "").strip().lower()
    if not upload or not upload.filename:
        return redirect(url_for("admin.overview", error=t("studio.no_file")))
    if not slug:
        slug = re.sub(r"[^a-z0-9-]+", "-", Path(upload.filename).stem.lower()).strip("-")
    err = validate_new_slug(slug, t)
    if err:
        return redirect(url_for("admin.overview", error=err))
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / slug
        dst.mkdir()
        try:
            names = extract_zip(upload.stream, dst, PROJECT_EXT, strip_single_root=True)
        except ValueError as exc:
            return redirect(url_for("admin.overview", error=str(exc)))
        if "project.yaml" not in names:
            return redirect(url_for("admin.overview", error=t("studio.no_project_yaml")))
        problems = install(dst, slug)
    if problems:
        return redirect(url_for("admin.overview", error="; ".join(problems[:3])))
    return redirect(url_for("studio.home", slug=slug, created=1))


def extract_zip(stream, dest, allowed_ext, strip_single_root=False):
    """Extracts a zip safely: no path traversal, allowed file types only, bounded size.
    Returns the relative paths written. Raises ValueError with a readable message."""
    data = io.BytesIO(stream.read())
    try:
        zf = zipfile.ZipFile(data)
    except zipfile.BadZipFile:
        raise ValueError("Not a valid zip file.")
    members = [m for m in zf.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")
               and not Path(m.filename).name.startswith(".")]
    if len(members) > ZIP_MAX_FILES:
        raise ValueError(f"Zip has more than {ZIP_MAX_FILES} files.")
    if sum(m.file_size for m in members) > ZIP_MAX_BYTES:
        raise ValueError("Zip is too large once extracted.")
    parts_list = []
    for m in members:
        parts = safe_parts(m.filename)
        if parts is None:
            raise ValueError(f"Unsafe path in zip: {m.filename}")
        parts_list.append((m, parts))
    if strip_single_root and parts_list and not any(p == ["project.yaml"] for _, p in parts_list):
        roots = {p[0] for _, p in parts_list}
        if len(roots) == 1 and all(len(p) > 1 for _, p in parts_list):
            parts_list = [(m, p[1:]) for m, p in parts_list]
    written = []
    skipped = []
    for m, parts in parts_list:
        rel = "/".join(parts)
        if Path(rel).suffix.lower() not in allowed_ext:
            skipped.append(rel)
            continue
        target = inside(dest, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(m) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        written.append(rel)
    return written


# ---------------------------------------------------------------- project studio

MODULE_TYPE_LABELS = {
    "survey": "Survey",
    "ab_test": "A/B test",
    "first_click": "First click",
    "card_sort": "Card sort",
    "tree_test": "Tree test",
}

FILE_ICONS = {
    "code": "fa-file-code",
    "image": "fa-file-image",
    "font": "fa-font",
    "video": "fa-file-video",
    "pdf": "fa-file-pdf",
    "folder": "fa-folder",
}

FILE_KINDS = {
    "html": "code", "htm": "code", "css": "code", "js": "code", "mjs": "code", "json": "code",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "svg": "image", "webp": "image",
    "woff": "font", "woff2": "font", "ttf": "font", "otf": "font",
    "mp4": "video", "webm": "video",
    "pdf": "pdf"
}


def format_bytes(n):
    if n < 1024:
        return f"{n} B"
    elif n < 1048576:
        return f"{n / 1024:.1f} KB"
    else:
        return f"{n / 1048576:.1f} MB"


def prototype_file_usage(project):
    """Returns a dict mapping rel_path -> dict(id=..., title=..., note=...) or dict(loaded_by=...)."""
    usage = {}
    proto_dir = project.dir / "prototypes"
    if not proto_dir.is_dir():
        return usage

    for mid, m in project.modules.items():
        m_file = project.dir / "modules" / f"{mid}.yaml"
        if m_file.is_file():
            text = m_file.read_text(encoding="utf-8", errors="ignore")
            for p in proto_dir.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(project.dir).as_posix()
                    p_name = p.name
                    if rel in text or f"prototypes/{p_name}" in text or f": {p_name}" in text:
                        note = "Used by module"
                        if m.type == "ab_test":
                            if "variant-a" in rel or "variant_a" in rel:
                                note = "Entry file for variant A"
                            elif "variant-b" in rel or "variant_b" in rel:
                                note = "Entry file for variant B"
                            else:
                                note = "Used in A/B test"
                        elif m.type == "first_click":
                            note = "Screenshot image"
                        usage[rel] = {"id": m.id, "title": m.title, "note": note}

    html_files = [p for p in proto_dir.rglob("*.html") if p.is_file()] + [p for p in proto_dir.rglob("*.htm") if p.is_file()]
    for p in proto_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() not in (".html", ".htm"):
            rel = p.relative_to(project.dir).as_posix()
            loaded_by = 0
            for h in html_files:
                if h != p:
                    content = h.read_text(encoding="utf-8", errors="ignore")
                    if p.name in content:
                        loaded_by += 1
            if loaded_by > 0 and rel not in usage:
                usage[rel] = {"loaded_by": loaded_by}

    return usage


@bp.route("/app/p/<slug>/studio/")
@bp.route("/<slug>/admin/studio/")
def home(slug):
    project = studio_project(slug, write=False)
    ctx = studio_ctx(project)

    n_participants = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    last_act = ctx.conn.execute("SELECT MAX(started_at) FROM sessions").fetchone()[0]
    last_activity_text = "Recently"
    if last_act:
        try:
            dt = datetime.fromisoformat(str(last_act).replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - dt
            if diff.days == 0:
                last_activity_text = "Today"
            elif diff.days == 1:
                last_activity_text = "Yesterday"
            else:
                last_activity_text = f"{diff.days} days ago"
        except Exception:
            last_activity_text = "Recently"

    started_map = {r["module_id"]: r["n"] for r in ctx.conn.execute(
        "SELECT module_id, COUNT(*) AS n FROM sessions GROUP BY module_id")}
    finished_map = {r["module_id"]: r["n"] for r in ctx.conn.execute(
        "SELECT module_id, COUNT(*) AS n FROM sessions WHERE finished_at IS NOT NULL GROUP BY module_id")}

    modules_by_id = {m.id: m for m in project.modules.values()}
    mod_list = []
    total_minutes = 0
    no_est = 0
    for m in project.modules.values():
        mins = m.minutes
        if mins:
            total_minutes += mins
        else:
            no_est += 1
        req_titles = [modules_by_id[r].title for r in m.requires if r in modules_by_id]
        mod_list.append({
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "type_label": MODULE_TYPE_LABELS.get(m.type, m.type),
            "minutes": mins,
            "requires": m.requires,
            "requires_titles": req_titles,
            "started": started_map.get(m.id, 0),
            "finished": finished_map.get(m.id, 0),
        })

    proto = project.dir / "prototypes"
    n_files = 0
    if proto.is_dir():
        for p in proto.rglob("*"):
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(project.dir).parts):
                n_files += 1

    return ctx.render(
        "admin/build.html",
        mod_list=mod_list,
        total_minutes=total_minutes,
        no_est=no_est,
        limit=30,
        n_files=n_files,
        n_participants=n_participants,
        last_activity_text=last_activity_text,
        is_super=is_super(),
        role="owner" if can_edit(slug) else "viewer",
        notice=request.args.get("notice"),
        error=request.args.get("error"),
        created=request.args.get("created"),
    )


@bp.route("/app/p/<slug>/studio/prototypes")
@bp.route("/<slug>/admin/studio/prototypes")
def prototypes(slug):
    project = studio_project(slug, write=False)
    ctx = studio_ctx(project)

    n_participants = ctx.conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    last_act = ctx.conn.execute("SELECT MAX(started_at) FROM sessions").fetchone()[0]
    last_activity_text = "Recently"
    if last_act:
        try:
            dt = datetime.fromisoformat(str(last_act).replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - dt
            if diff.days == 0:
                last_activity_text = "Today"
            elif diff.days == 1:
                last_activity_text = "Yesterday"
            else:
                last_activity_text = f"{diff.days} days ago"
        except Exception:
            last_activity_text = "Recently"

    usage = prototype_file_usage(project)
    proto = project.dir / "prototypes"
    files = []
    total_bytes = 0
    if proto.is_dir():
        for p in sorted(proto.rglob("*")):
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(project.dir).parts):
                rel = p.relative_to(project.dir).as_posix()
                sz = p.stat().st_size
                total_bytes += sz
                e = p.suffix.lower().lstrip(".")
                kind = FILE_KINDS.get(e, "code")
                icon = FILE_ICONS.get(kind, "fa-file-code")
                is_prev = e in ("html", "htm", "png", "jpg", "jpeg", "gif", "svg", "webp", "pdf")
                raw_url = url_for("studio.files_raw", slug=project.slug, rel=rel)
                u = usage.get(rel)
                locked = bool(u and "id" in u)
                files.append({
                    "rel": rel,
                    "name": p.name,
                    "size": sz,
                    "size_formatted": format_bytes(sz),
                    "icon_class": icon,
                    "is_previewable": is_prev,
                    "src": raw_url if is_prev else None,
                    "use": u if u and "id" in u else None,
                    "loaded_by": u.get("loaded_by") if u and "loaded_by" in u else None,
                    "locked": locked,
                })

    limit_mb = 100
    quota_pct = max(1, min(100, round((total_bytes / (limit_mb * 1024 * 1024)) * 100)))

    return ctx.render(
        "admin/prototypes.html",
        files=files,
        total_size_formatted=format_bytes(total_bytes),
        quota_pct=quota_pct,
        n_files=len(files),
        n_participants=n_participants,
        last_activity_text=last_activity_text,
        is_super=is_super(),
        role="owner" if can_edit(slug) else "viewer",
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


@bp.route("/app/p/<slug>/studio/edit", methods=["GET", "POST"])
@bp.route("/<slug>/admin/studio/edit", methods=["GET", "POST"])
def edit(slug):
    project = studio_project(slug, write=request.method == "POST")
    rel = request.values.get("file", "project.yaml")
    check_editable_rel(rel)
    path = project.dir / rel
    t = translator(project.locale)
    problems, status = [], None
    if request.method == "POST":
        text = request.form.get("content", "").replace("\r\n", "\n")
        problems = trial(project.dir, slug, writes={rel: text})
        if not problems and request.form.get("action") == "save":
            write_text(project.dir, rel, text)
            reload()
            with storage.connect(slug) as conn:
                storage.audit(conn, "edit_file", request.remote_addr, {"file": rel})
            return redirect(url_for("studio.edit", slug=slug, file=rel, saved=1))
        status = "invalid" if problems else "valid"
    else:
        restore = request.args.get("restore")
        if restore:
            src = inside(project.dir / ".history", restore)
            if not src.is_file():
                abort(404)
            text, status = src.read_text(encoding="utf-8"), "restored"
        elif path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            abort(404)
    ctx = studio_ctx(project)
    mid = rel[len("modules/"):-len(".yaml")] if rel.startswith("modules/") else None
    started = ctx.conn.execute("SELECT COUNT(*) FROM sessions WHERE module_id = ?", (mid,)).fetchone()[0] if mid else 0
    return ctx.render("admin/studio_edit.html", rel=rel, text=text, problems=problems, status=status,
                      saved=request.args.get("saved"), started=started, files=editable_files(project.dir),
                      history=history(project.dir, rel)[:10] if project.editable else [])


@bp.post("/app/p/<slug>/studio/modules/new")
@bp.post("/<slug>/admin/studio/modules/new")
def module_new(slug):
    project = studio_project(slug)
    t = translator(project.locale)
    mid = request.form.get("id", "").strip().lower()
    title = request.form.get("title", "").strip()
    mtype = request.form.get("type", "survey")
    if not ID_RE.match(mid) or mtype not in MODULE_TYPES:
        return redirect(url_for("studio.home", slug=slug, error=t("studio.bad_module_id")))
    if (project.dir / "modules" / f"{mid}.yaml").exists() or mid in project.modules:
        return redirect(url_for("studio.home", slug=slug, error=t("studio.module_exists", id=mid)))
    template = (SCAFFOLD / "templates" / f"{mtype}.yaml").read_text(encoding="utf-8")
    if title:
        template = re.sub(r"^title:.*$", f"title: {title}", template, count=1, flags=re.MULTILINE)
    data = read_project_yaml(project.dir)
    data["modules"] = list(data.get("modules") or []) + [mid]
    writes = {"project.yaml": dump_yaml(data), f"modules/{mid}.yaml": template}
    if mtype == "ab_test":
        for name in ("variant-a.html", "variant-b.html"):
            if not (project.dir / "prototypes" / name).exists():
                writes[f"prototypes/{name}"] = (SCAFFOLD / "templates" / name).read_text(encoding="utf-8")
    problems = trial(project.dir, slug, writes=writes)
    if problems:
        return redirect(url_for("studio.home", slug=slug, error="; ".join(problems[:3])))
    for rel, text in writes.items():
        write_text(project.dir, rel, text)
    reload()
    return redirect(url_for("studio.module_edit", slug=slug, mid=mid, file=f"modules/{mid}.yaml"))


def module_to_ui_data(mid, m, data):
    """Normalizes raw YAML dictionary into the state object expected by admin/module.html."""
    mtype = data.get("type") or (m.type if m else "ab_test")
    title = str(data.get("title") or (m.title if m else mid))
    desc = str(data.get("description") or (m.description if m else ""))
    mins = data.get("minutes")
    if mins is None and m:
        mins = m.minutes
    mins = mins if mins is not None else ""
    reqs = list(data.get("requires") or (m.requires if m else []))
    aud = data.get("audience") or (m.audience if m else {}) or {}

    out = {
        "title": title,
        "description": desc,
        "minutes": mins,
        "requires": reqs,
        "audPattern": str(aud.get("identity_pattern") or ""),
        "audModule": str(aud.get("module") or ""),
        "audQuestion": str(aud.get("question") or ""),
        "audOp": str(aud.get("op") or "equals"),
        "audValue": str(aud.get("value") or ""),
        "yaml": None,
    }

    if mtype == "ab_test":
        variants = []
        raw_vars = data.get("variants") or {}
        if isinstance(raw_vars, dict):
            for k, v in raw_vars.items():
                v = v or {}
                variants.append({
                    "key": str(k),
                    "label": str(v.get("label") or k),
                    "file": str(v.get("file") or ""),
                })
        out["variants"] = variants
        out["baseline"] = str(data.get("baseline") or (variants[0]["key"] if variants else "A"))
        out["order"] = str(data.get("order") or "rotate")
        out["intro"] = str(data.get("intro") or "")

        tasks = []
        for i, t in enumerate(data.get("tasks") or []):
            tid = str(t.get("id") or f"task_{i+1}")
            fields = t.get("fields") or []
            f0 = fields[0] if fields else {}
            kind = f0.get("kind", "text")
            options = f0.get("options") or []
            options_str = "\n".join(str(o) for o in options) if isinstance(options, list) else str(options)
            accept_map = t.get("accept") or {}
            if isinstance(accept_map, dict):
                accept_list = accept_map.get(f0.get("id", tid)) or accept_map.get(tid) or []
                if not accept_list and accept_map:
                    accept_list = next(iter(accept_map.values()), [])
            elif isinstance(accept_map, list):
                accept_list = accept_map
            else:
                accept_list = [str(accept_map)]
            tasks.append({
                "id": tid,
                "title": str(t.get("title") or tid),
                "prompt": str(t.get("prompt") or ""),
                "fieldLabel": str(f0.get("label") or "Your answer"),
                "placeholder": str(f0.get("placeholder") or ""),
                "kind": kind,
                "options": options_str,
                "accept": [str(a) for a in accept_list],
                "probe": str(t.get("probe") or ""),
            })
        out["tasks"] = tasks
        out["ease"] = bool(data.get("ease_question", True))

        def norm_qs(qs):
            res = []
            for q in qs or []:
                labels = q.get("labels") or []
                low = str(labels[0]) if len(labels) > 0 else str(q.get("low") or "")
                high = str(labels[-1]) if len(labels) > 1 else str(q.get("high") or "")
                opts = q.get("options") or []
                opts_str = "\n".join(str(o) for o in opts) if isinstance(opts, list) else str(opts)
                res.append({
                    "id": str(q.get("id") or ""),
                    "type": str(q.get("type") or "scale"),
                    "label": str(q.get("label") or ""),
                    "required": bool(q.get("required", True)),
                    "points": int(q.get("points") or 5),
                    "low": low,
                    "high": high,
                    "options": opts_str,
                })
            return res

        out["post"] = norm_qs(data.get("post_survey") or data.get("post") or [])
        out["preference"] = bool(data.get("preference", len(variants) > 1))
        out["final"] = norm_qs(data.get("final_survey") or data.get("final") or [])

        rule = data.get("decision_rule") or {}
        sqs = list(rule.get("survey_questions") or [])
        out["rule"] = {
            "min": int(rule.get("min_participants", 8)),
            "question": str(sqs[0]) if sqs else "",
            "gain": float(rule.get("min_survey_gain", 0.5)),
        }

    elif mtype == "survey":
        pages = data.get("pages")
        if pages is None and "questions" in data:
            pages = [{"title": "Questions", "questions": data["questions"]}]
        pages = pages or []
        ui_pages = []
        for pi, page in enumerate(pages):
            p_title = str(page.get("title") or f"Page {pi+1}")
            qs = []
            for qi, q in enumerate(page.get("questions") or []):
                labels = q.get("labels") or []
                low = str(labels[0]) if len(labels) > 0 else str(q.get("low") or "")
                high = str(labels[-1]) if len(labels) > 1 else str(q.get("high") or "")
                raw_opts = q.get("options") or []
                if isinstance(raw_opts, dict):
                    opts_str = "\n".join(str(v) for v in raw_opts.values())
                elif isinstance(raw_opts, list):
                    opts_str = "\n".join(str(o) for o in raw_opts)
                else:
                    opts_str = str(raw_opts)
                raw_rows = q.get("rows") or []
                rows_str = "\n".join(str(r) for r in raw_rows) if isinstance(raw_rows, list) else str(raw_rows)
                show_if = q.get("show_if") or {}
                on = bool(show_if)
                op = "equals"
                val = ""
                if "equals" in show_if:
                    op = "equals"
                    val = str(show_if["equals"])
                elif "in" in show_if:
                    op = "in"
                    val = ", ".join(str(x) for x in show_if["in"])
                elif "not_in" in show_if:
                    op = "not_in"
                    val = ", ".join(str(x) for x in show_if["not_in"])
                ref_q = str(show_if.get("question") or show_if.get("ref") or "")
                qs.append({
                    "id": str(q.get("id") or f"q_{pi+1}_{qi+1}"),
                    "type": str(q.get("type") or "single"),
                    "label": str(q.get("label") or ""),
                    "required": bool(q.get("required", True)),
                    "points": int(q.get("points") or 5),
                    "low": low,
                    "high": high,
                    "options": opts_str,
                    "rows": rows_str,
                    "max": q.get("max", ""),
                    "showIf": {
                        "on": on,
                        "module": str(show_if.get("module") or ""),
                        "q": ref_q,
                        "op": op,
                        "value": val,
                    },
                })
            ui_pages.append({"title": p_title, "questions": qs})
        if not ui_pages:
            ui_pages = [{"title": "Page 1", "questions": []}]
        out["pages"] = ui_pages

    elif mtype == "tree_test":
        out["intro"] = str(data.get("instructions") or data.get("intro") or "")

        def tree_to_lines(nodes, depth=0):
            res = []
            for n in nodes or []:
                if isinstance(n, dict):
                    name = str(n.get("label") or n.get("name") or n.get("id") or "")
                    res.append("  " * depth + name)
                    if n.get("children"):
                        res.extend(tree_to_lines(n["children"], depth + 1))
                elif isinstance(n, str):
                    res.append("  " * depth + n)
            return res
        out["outline"] = "\n".join(tree_to_lines(data.get("tree") or []))

        id_to_path = {}

        def map_ids(nodes, stack=()):
            for n in nodes or []:
                if isinstance(n, dict):
                    lbl = str(n.get("label") or n.get("name") or n.get("id") or "")
                    cur = stack + (lbl,)
                    nid = str(n.get("id") or "")
                    if nid:
                        id_to_path[nid] = " > ".join(cur)
                    id_to_path[lbl] = " > ".join(cur)
                    if n.get("children"):
                        map_ids(n["children"], cur)
        map_ids(data.get("tree") or [])

        tasks = []
        for i, t in enumerate(data.get("tasks") or []):
            raw_acc = t.get("accept") or t.get("correct") or []
            correct = [id_to_path.get(str(x), str(x)) for x in raw_acc]
            tasks.append({
                "id": str(t.get("id") or f"task_{i+1}"),
                "prompt": str(t.get("prompt") or ""),
                "correct": correct,
            })
        out["tasks"] = tasks

    elif mtype == "card_sort":
        out["intro"] = str(data.get("instructions") or data.get("intro") or "")
        cards = data.get("cards") or []
        card_lines = []
        for c in cards:
            if isinstance(c, dict):
                card_lines.append(str(c.get("label") or c.get("id") or ""))
            else:
                card_lines.append(str(c))
        out["cards"] = "\n".join(card_lines)
        cats = data.get("categories") or []
        out["categories"] = "\n".join(str(c) for c in cats)
        allow_new = data.get("allow_new_categories", not bool(cats))
        if not cats:
            out["mode"] = "open"
        elif allow_new:
            out["mode"] = "hybrid"
        else:
            out["mode"] = "closed"

    elif mtype == "first_click":
        out["intro"] = str(data.get("instructions") or data.get("intro") or "")
        tasks = []
        for i, t in enumerate(data.get("tasks") or []):
            tgt = t.get("target") or {}
            tasks.append({
                "id": str(t.get("id") or f"task_{i+1}"),
                "prompt": str(t.get("prompt") or ""),
                "image": str(t.get("image") or ""),
                "x": int(tgt.get("x", 0)),
                "y": int(tgt.get("y", 0)),
                "w": int(tgt.get("width") or tgt.get("w") or 100),
                "h": int(tgt.get("height") or tgt.get("h") or 40),
                "label": str(tgt.get("name") or tgt.get("label") or ""),
            })
        out["tasks"] = tasks

    return out


@bp.route("/app/p/<slug>/studio/modules/<mid>", methods=["GET", "POST"])
@bp.route("/<slug>/admin/studio/modules/<mid>", methods=["GET", "POST"])
def module_edit(slug, mid):
    if not ID_RE.match(mid):
        abort(400)
    project = studio_project(slug, write=request.method == "POST")
    rel = f"modules/{mid}.yaml"
    path = project.dir / rel
    if not path.is_file() and mid not in project.modules:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "save")
        content = request.form.get("content", "").replace("\r\n", "\n")
        writes = {rel: content}
        problems = trial(project.dir, slug, writes=writes)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json
        if problems:
            if is_ajax:
                return jsonify({"ok": False, "problems": problems})
            return redirect(url_for("studio.module_edit", slug=slug, mid=mid, error="; ".join(problems[:3])))
        if action == "check":
            if is_ajax:
                return jsonify({"ok": True, "problems": []})
            return redirect(url_for("studio.module_edit", slug=slug, mid=mid, notice="Check passed. No problems found."))
        if action == "save":
            write_text(project.dir, rel, content)
            reload()
            with storage.connect(slug) as conn:
                storage.audit(conn, "edit_module", request.remote_addr, {"mid": mid, "file": rel})
            if is_ajax:
                return jsonify({"ok": True})
            return redirect(url_for("studio.module_edit", slug=slug, mid=mid, saved=1))
        if is_ajax:
            return jsonify({"ok": False, "problems": [f"Unknown action: {action}"]})
        abort(400)

    # GET request
    restore = request.args.get("restore")
    if restore:
        src = inside(project.dir / ".history", restore)
        if not src.is_file():
            abort(404)
        raw_yaml = src.read_text(encoding="utf-8")
    elif path.is_file():
        raw_yaml = path.read_text(encoding="utf-8")
    else:
        abort(404)

    try:
        data = yaml.safe_load(raw_yaml) or {}
    except Exception:
        data = {}

    m_obj = project.modules.get(mid)
    m_type = data.get("type") or (m_obj.type if m_obj else "ab_test")
    initial_data = module_to_ui_data(mid, m_obj, data)

    # Available prototype files
    prototypes_dir = project.dir / "prototypes"
    available_files = []
    if prototypes_dir.is_dir():
        for f in sorted(prototypes_dir.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                available_files.append(str(f.relative_to(project.dir)).replace("\\", "/"))

    # Other modules
    other_modules = [{"id": m.id, "title": m.title, "type": m.type} for m in project.modules.values()]

    type_label_map = {
        "ab_test": "A/B test",
        "survey": "Survey",
        "tree_test": "Tree test",
        "card_sort": "Card sort",
        "first_click": "First click",
    }

    ctx = studio_ctx(project)
    started = ctx.conn.execute("SELECT COUNT(*) FROM sessions WHERE module_id = ?", (mid,)).fetchone()[0] if mid else 0
    hist = history(project.dir, rel)[:10] if project.editable else []

    can_edit_flag = can_edit(slug) and project.editable

    return ctx.render(
        "admin/module.html",
        module=m_obj or {"id": mid, "title": data.get("title", mid), "type": m_type},
        module_type=m_type,
        module_type_label=type_label_map.get(m_type, m_type),
        started=started,
        can_edit=can_edit_flag,
        available_files=available_files,
        other_modules=other_modules,
        initial_data=initial_data,
        raw_yaml=raw_yaml,
        history=hist,
    )


@bp.post("/app/p/<slug>/studio/modules/<mid>/duplicate")
@bp.post("/<slug>/admin/studio/modules/<mid>/duplicate")
def module_duplicate(slug, mid):
    project = studio_project(slug)
    if not ID_RE.match(mid) or mid not in project.modules:
        abort(400)

    new_id = f"{mid}-copy"
    n = 2
    while (project.dir / "modules" / f"{new_id}.yaml").exists() or new_id in project.modules:
        new_id = f"{mid}-copy-{n}"
        n += 1

    src_file = project.dir / "modules" / f"{mid}.yaml"
    if not src_file.is_file():
        abort(404)
    content = src_file.read_text(encoding="utf-8")
    orig_title = project.modules[mid].title
    new_title = f"{orig_title} (copy)"
    content = re.sub(r"^title:.*$", f"title: {new_title}", content, count=1, flags=re.MULTILINE)

    data = read_project_yaml(project.dir)
    current_mods = list(data.get("modules") or [])
    if mid in current_mods:
        idx = current_mods.index(mid)
        current_mods.insert(idx + 1, new_id)
    else:
        current_mods.append(new_id)
    data["modules"] = current_mods

    writes = {"project.yaml": dump_yaml(data), f"modules/{new_id}.yaml": content}
    problems = trial(project.dir, slug, writes=writes)
    if problems:
        return redirect(url_for("studio.home", slug=slug, error="; ".join(problems[:3])))
    for rel, text in writes.items():
        write_text(project.dir, rel, text)
    reload()
    return redirect(url_for("studio.home", slug=slug, notice=f"Duplicated {orig_title} as {new_title}."))


@bp.post("/app/p/<slug>/studio/modules/<mid>/move")
@bp.post("/<slug>/admin/studio/modules/<mid>/move")
def module_move(slug, mid):
    project = studio_project(slug)
    if not ID_RE.match(mid):
        abort(400)
    data = read_project_yaml(project.dir)
    current_mods = list(data.get("modules") or [])

    order = request.form.get("order")
    direction = request.form.get("direction")

    if order:
        new_order = [m.strip() for m in order.split(",") if m.strip()]
        if set(new_order) == set(current_mods) and len(new_order) == len(current_mods):
            current_mods = new_order
    elif direction in ("up", "down") and mid in current_mods:
        idx = current_mods.index(mid)
        if direction == "up" and idx > 0:
            current_mods[idx - 1], current_mods[idx] = current_mods[idx], current_mods[idx - 1]
        elif direction == "down" and idx < len(current_mods) - 1:
            current_mods[idx + 1], current_mods[idx] = current_mods[idx], current_mods[idx + 1]

    data["modules"] = current_mods
    writes = {"project.yaml": dump_yaml(data)}
    problems = trial(project.dir, slug, writes=writes)
    if problems:
        return redirect(url_for("studio.home", slug=slug, error="; ".join(problems[:3])))
    write_text(project.dir, "project.yaml", dump_yaml(data))
    reload()
    return redirect(url_for("studio.home", slug=slug, notice="Order saved."))


@bp.post("/app/p/<slug>/studio/modules/<mid>/delete")
@bp.post("/<slug>/admin/studio/modules/<mid>/delete")
def module_delete(slug, mid):
    project = studio_project(slug)
    t = translator(project.locale)
    if not ID_RE.match(mid):
        abort(400)
    data = read_project_yaml(project.dir)
    data["modules"] = [m for m in data.get("modules") or [] if str(m) != mid]
    rel = f"modules/{mid}.yaml"
    problems = trial(project.dir, slug, writes={"project.yaml": dump_yaml(data)}, deletes=[rel])
    if problems:
        return redirect(url_for("studio.home", slug=slug, error="; ".join(problems[:3])))
    write_text(project.dir, "project.yaml", dump_yaml(data))
    backup(project.dir, rel)
    (project.dir / rel).unlink(missing_ok=True)
    reload()
    return redirect(url_for("studio.home", slug=slug, notice=t("studio.module_deleted", id=mid)))


@bp.post("/app/p/<slug>/studio/files/upload")
@bp.post("/<slug>/admin/studio/files/upload")
def files_upload(slug):
    project = studio_project(slug)
    t = translator(project.locale)
    folder = safe_parts(request.form.get("folder", "")) or []
    base = inside(project.dir, "/".join(["prototypes"] + folder))
    written, skipped = [], []
    for f in request.files.getlist("files"):
        if not f or not f.filename:
            continue
        f.stream.seek(0, 2)
        size = f.stream.tell()
        f.stream.seek(0)
        over = limits.check_upload(project.dir, size)
        if over:
            return redirect(url_for("studio.prototypes", slug=slug, error=over))
        if f.filename.lower().endswith(".zip") and request.form.get("extract", "1") == "1":
            base.mkdir(parents=True, exist_ok=True)
            try:
                written += extract_zip(f.stream, base, PROTOTYPE_EXT)
            except ValueError as exc:
                return redirect(url_for("studio.prototypes", slug=slug, error=str(exc)))
            continue
        parts = safe_parts(f.filename)  # folder uploads send "dir/sub/file.css"
        if parts is None or Path(parts[-1]).suffix.lower() not in PROTOTYPE_EXT:
            skipped.append(f.filename)
            continue
        target = inside(base, "/".join(parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        f.save(target)
        written.append("/".join(parts))
    msg = t("studio.uploaded", n=len(written)) + (" " + t("studio.skipped", files=", ".join(skipped[:5])) if skipped else "")
    with storage.connect(slug) as conn:
        storage.audit(conn, "upload_files", request.remote_addr, {"written": written})
    return redirect(url_for("studio.prototypes", slug=slug, notice=msg))


@bp.post("/app/p/<slug>/studio/files/delete")
@bp.post("/<slug>/admin/studio/files/delete")
def files_delete(slug):
    project = studio_project(slug)
    t = translator(project.locale)
    parts = safe_parts(request.form.get("path", ""))
    if not parts or parts[0] != "prototypes":
        abort(400)
    rel = "/".join(parts)
    # Refuse when a module still points at the file, so a delete can't break a live study.
    problems = trial(project.dir, slug, deletes=[rel])
    if problems:
        return redirect(url_for("studio.prototypes", slug=slug, error=t("studio.file_in_use", file=rel)))
    target = inside(project.dir, rel)
    if target.is_file():
        target.unlink()
        with storage.connect(slug) as conn:
            storage.audit(conn, "delete_file", request.remote_addr, {"file": rel})
    return redirect(url_for("studio.prototypes", slug=slug, notice=t("studio.file_deleted", file=rel)))


@bp.route("/app/p/<slug>/studio/files/raw/<path:rel>")
@bp.route("/<slug>/admin/studio/files/raw/<path:rel>")
def files_raw(slug, rel):
    project = studio_project(slug, write=False)
    parts = safe_parts(rel)
    if not parts or parts[0] != "prototypes":
        abort(404)
    return send_from_directory(project.dir, "/".join(parts), max_age=0)


@bp.post("/app/p/<slug>/studio/passcodes")
@bp.post("/<slug>/admin/studio/passcodes")
def set_passcodes(slug):
    project = studio_project(slug, write=False)  # read-only projects can still get passcodes
    t = translator(project.locale)
    changed = []
    for kind in passcodes.KINDS:
        if request.form.get(f"clear_{kind}"):
            passcodes.clear(slug, kind)
            changed.append(kind)
            continue
        value = request.form.get(kind, "")
        if value:
            if len(value) < 6:
                return redirect(url_for("studio.home", slug=slug, error=t("studio.passcode_short")))
            passcodes.set_passcode(slug, kind, value)
            changed.append(kind)
    if changed:
        with storage.connect(slug) as conn:
            storage.audit(conn, "set_passcodes", request.remote_addr, {"changed": changed})
    return redirect(url_for("studio.home", slug=slug, notice=t("studio.passcodes_saved") if changed else None))


@bp.route("/app/p/<slug>/studio/export.zip")
@bp.route("/<slug>/admin/studio/export.zip")
def export_zip(slug):
    project = studio_project(slug, write=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(project.dir.rglob("*")):
            rel = p.relative_to(project.dir)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                zf.write(p, f"{slug}/{rel.as_posix()}")
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"{slug}.zip")


@bp.post("/<slug>/admin/studio/delete")
def delete_project(slug):
    project = get_project(slug)
    if not is_super() or not project.editable:
        abort(403)
    if request.form.get("confirm") != slug:
        return redirect(url_for("studio.home", slug=slug, error=translator(project.locale)("studio.confirm_mismatch")))
    target = inside(registry().studio, slug)
    if target.parent != registry().studio.resolve():
        abort(400)
    shutil.rmtree(target)
    if request.form.get("with_data"):
        storage.close_all()
        storage_path = Path(current_app.config["DATA_DIR"]) / f"{slug}.db"
        storage_path.unlink(missing_ok=True)
        passcodes.clear(slug)
    reload()
    return redirect(url_for("admin.overview", notice=f"Deleted {slug}"))
