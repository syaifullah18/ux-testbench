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
from flask import (Blueprint, abort, current_app, redirect, render_template, request, send_file,
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
    return redirect(url_for("studio.edit", slug=slug, file=f"modules/{mid}.yaml"))


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
