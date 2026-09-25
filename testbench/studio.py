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


def project_yaml_text(name, locale, mode, access, description=""):
    identity = {"code": '  mode: code\n  pattern: "^P\\\\d{2}$"      # codes you hand out, e.g. P01\n',
                "email": "  mode: email\n  # domains: [example.com]\n",
                "anonymous": "  mode: anonymous          # the app issues a resume code\n"}[mode]
    return (f"name: {yaml.safe_dump(name, allow_unicode=True).strip().removesuffix('...').strip()}\n"
            f"description: {yaml.safe_dump(description or 'Describe the study for participants.', allow_unicode=True).strip().removesuffix('...').strip()}\n"
            f"locale: {locale}\nlisted: false\n\nidentity:\n{identity}\n"
            f"access: {access}               # passcode | open; set passcodes in the Studio\n\n"
            "consent: >-\n  I agree that my answers are recorded for this research.\n\n"
            'brand:\n  primary: "#2563EB"\n  nav: "#0F172A"\n\n'
            "modules:\n  - feedback\n")


def studio_ctx(project):
    return Ctx(project, admin=True)


# ---------------------------------------------------------------- superadmin: create, import

@bp.post("/admin/projects/new")
def new_project():
    if not is_super():
        abort(403)
    t = translator("en")
    slug = request.form.get("slug", "").strip().lower()
    err = validate_new_slug(slug, t)
    source = request.form.get("source", "blank")
    if err:
        return redirect(url_for("admin.overview", error=err))
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / slug
        if source == "blank":
            (dst / "modules").mkdir(parents=True)
            mode = request.form.get("identity", "code")
            (dst / "project.yaml").write_text(project_yaml_text(
                request.form.get("name", "").strip() or slug,
                request.form.get("locale") if request.form.get("locale") in available_locales() else "en",
                mode if mode in ("code", "email", "anonymous") else "code",
                "open" if request.form.get("access") == "open" else "passcode"), encoding="utf-8")
            shutil.copy(SCAFFOLD / "modules" / "feedback.yaml", dst / "modules" / "feedback.yaml")
        else:
            src = registry().get(source)
            if src is None:
                abort(404)
            shutil.copytree(src.dir, dst, ignore=shutil.ignore_patterns(".history"))
            data = read_project_yaml(dst)
            data["name"] = request.form.get("name", "").strip() or f"{data.get('name', source)} (copy)"
            data.pop("passcode_env", None)
            data.pop("admin_passcode_env", None)
            (dst / "project.yaml").write_text(dump_yaml(data), encoding="utf-8")
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

@bp.route("/app/p/<slug>/studio/")
@bp.route("/<slug>/admin/studio/")
def home(slug):
    project = studio_project(slug, write=False)
    ctx = studio_ctx(project)
    started = {r["module_id"]: r["n"] for r in ctx.conn.execute(
        "SELECT module_id, COUNT(*) AS n FROM sessions GROUP BY module_id")}
    files = []
    proto = project.dir / "prototypes"
    if proto.is_dir():
        for p in sorted(proto.rglob("*")):
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(project.dir).parts):
                files.append({"rel": p.relative_to(project.dir).as_posix(), "size": p.stat().st_size})
    pc = {k: passcodes.source(project, k) for k in passcodes.KINDS}
    return ctx.render("admin/studio.html", started=started, files=files, pc=pc, is_super=is_super(),
                      history=history(project.dir)[:10] if project.editable else [],
                      module_types=sorted(MODULE_TYPES), env_prefix=env_prefix(slug),
                      notice=request.args.get("notice"), error=request.args.get("error"),
                      created=request.args.get("created"))


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


@bp.post("/<slug>/admin/studio/modules/new")
def module_new(slug):
    project = studio_project(slug)
    t = translator(project.locale)
    mid = request.form.get("id", "").strip().lower()
    mtype = request.form.get("type", "survey")
    if not ID_RE.match(mid) or mtype not in MODULE_TYPES:
        return redirect(url_for("studio.home", slug=slug, error=t("studio.bad_module_id")))
    if (project.dir / "modules" / f"{mid}.yaml").exists() or mid in project.modules:
        return redirect(url_for("studio.home", slug=slug, error=t("studio.module_exists", id=mid)))
    template = (SCAFFOLD / "templates" / f"{mtype}.yaml").read_text(encoding="utf-8")
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
            return redirect(url_for("studio.home", slug=slug, error=over))
        if f.filename.lower().endswith(".zip") and request.form.get("extract", "1") == "1":
            base.mkdir(parents=True, exist_ok=True)
            try:
                written += extract_zip(f.stream, base, PROTOTYPE_EXT)
            except ValueError as exc:
                return redirect(url_for("studio.home", slug=slug, error=str(exc)))
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
    return redirect(url_for("studio.home", slug=slug, notice=msg))


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
        return redirect(url_for("studio.home", slug=slug, error=t("studio.file_in_use", file=rel)))
    target = inside(project.dir, rel)
    if target.is_file():
        target.unlink()
        with storage.connect(slug) as conn:
            storage.audit(conn, "delete_file", request.remote_addr, {"file": rel})
    return redirect(url_for("studio.home", slug=slug, notice=t("studio.file_deleted", file=rel)))


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
