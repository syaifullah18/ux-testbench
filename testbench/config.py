"""Loads and validates projects from YAML.

A project is a folder with a `project.yaml` and one YAML file per module under `modules/`.
Validation collects every problem it finds instead of stopping at the first, so `python -m
testbench check` can report a whole broken config in one pass.
"""
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
RESERVED_SLUGS = {"static", "admin", "api", "health"}
IDENTITY_MODES = {"code", "email", "anonymous"}
ACCESS_MODES = {"passcode", "open"}
DEFAULT_BRAND = {"primary": "#2563EB", "nav": "#0F172A"}
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__("Invalid project configuration:\n  - " + "\n  - ".join(problems))


class Problems(list):
    """A list of messages, each prefixed with where it was found."""

    def at(self, where, msg):
        self.append(f"{where}: {msg}")


@dataclass
class Module:
    id: str
    type: str
    title: str
    description: str
    minutes: int | None
    requires: list
    audience: dict
    conf: dict               # the type-specific part, normalised by the module type
    project: "Project" = field(repr=False, default=None)
    impl: object = field(repr=False, default=None)


@dataclass
class Project:
    slug: str
    dir: Path
    name: str
    description: str
    locale: str
    brand: dict
    listed: bool
    identity: dict
    access: str
    consent: str
    passcode_env: str
    admin_passcode_env: str
    modules: dict            # id -> Module, in home-screen order
    editable: bool = False   # lives in the Studio folder, so the admin site may change it

    def module(self, module_id):
        return self.modules.get(module_id)


def env_prefix(slug):
    return re.sub(r"[^A-Z0-9]", "_", slug.upper())


def read_yaml(path, problems):
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        problems.at(path, f"cannot read YAML ({exc})")
        return None
    if not isinstance(data, dict):
        problems.at(path, "top level must be a mapping")
        return None
    return data


def as_text(value, default=""):
    return default if value is None else str(value).strip()


def load_project(pdir, module_types, problems):
    pdir = Path(pdir)
    slug = pdir.name
    where = f"{slug}/project.yaml"
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        problems.at(where, f"folder name '{slug}' must be lowercase letters, digits and hyphens, "
                           f"and not one of {sorted(RESERVED_SLUGS)}")
        return None
    raw = read_yaml(pdir / "project.yaml", problems)
    if raw is None:
        return None

    identity = raw.get("identity") or {"mode": "code"}
    if isinstance(identity, str):
        identity = {"mode": identity}
    mode = identity.get("mode", "code")
    if mode not in IDENTITY_MODES:
        problems.at(where, f"identity.mode must be one of {sorted(IDENTITY_MODES)}")
    if mode == "code":
        pattern = identity.get("pattern", r"^[A-Z0-9-]{2,20}$")
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.at(where, f"identity.pattern is not a valid regex ({exc})")
        identity["pattern"] = pattern
    if mode == "email":
        domains = identity.get("domains") or []
        identity["domains"] = [d.lower().lstrip("@") for d in ([domains] if isinstance(domains, str) else domains)]
    identity["mode"] = mode

    access = raw.get("access", "passcode")
    if access not in ACCESS_MODES:
        problems.at(where, f"access must be one of {sorted(ACCESS_MODES)}")

    brand = {**DEFAULT_BRAND, **(raw.get("brand") or {})}
    for key, val in brand.items():
        if not HEX_RE.match(str(val)):
            problems.at(where, f"brand.{key} must be a #RRGGBB colour")

    prefix = env_prefix(slug)
    project = Project(
        slug=slug, dir=pdir,
        name=as_text(raw.get("name"), slug),
        description=as_text(raw.get("description")),
        locale=as_text(raw.get("locale"), "en"),
        brand=brand,
        listed=bool(raw.get("listed", True)),
        identity=identity,
        access=access,
        consent=as_text(raw.get("consent")),
        passcode_env=as_text(raw.get("passcode_env"), f"{prefix}_PASSCODE"),
        admin_passcode_env=as_text(raw.get("admin_passcode_env"), f"{prefix}_ADMIN_PASSCODE"),
        modules={},
    )

    order = raw.get("modules")
    if not isinstance(order, list) or not order:
        problems.at(where, "modules must be a non-empty list of module ids (file names under modules/)")
        return project
    for mid in order:
        mid = str(mid)
        mwhere = f"{slug}/modules/{mid}.yaml"
        if not ID_RE.match(mid):
            problems.at(where, f"module id '{mid}' must be lowercase letters, digits, '-' or '_'")
            continue
        if mid in project.modules:
            problems.at(where, f"module '{mid}' is listed twice")
            continue
        mraw = read_yaml(pdir / "modules" / f"{mid}.yaml", problems)
        if mraw is None:
            continue
        mtype = mraw.get("type")
        if mtype not in module_types:
            problems.at(mwhere, f"type must be one of {sorted(module_types)}")
            continue
        requires = [str(r) for r in (mraw.get("requires") or [])]
        audience = mraw.get("audience") or {}
        if "identity_pattern" in audience:
            try:
                re.compile(audience["identity_pattern"])
            except re.error as exc:
                problems.at(mwhere, f"audience.identity_pattern is not a valid regex ({exc})")
        module = Module(
            id=mid, type=mtype,
            title=as_text(mraw.get("title"), mid),
            description=as_text(mraw.get("description")),
            minutes=mraw.get("minutes"),
            requires=requires, audience=audience, conf={}, project=project,
        )
        impl = module_types[mtype](module)
        module.conf = impl.validate(mraw, Scope(problems, mwhere))
        module.impl = impl
        project.modules[mid] = module

    for m in project.modules.values():
        for r in m.requires:
            if r not in project.modules:
                problems.at(f"{slug}/modules/{m.id}.yaml", f"requires unknown module '{r}'")
        when = m.audience.get("when")
        if when:
            check_ref(project, m.id, when.get("ref"), problems, f"{slug}/modules/{m.id}.yaml audience.when")
    for m in project.modules.values():
        m.impl.check_refs(Scope(problems, f"{slug}/modules/{m.id}.yaml"))
    return project


def check_ref(project, module_id, ref, problems, where):
    """A ref is 'question' (same module) or 'module.question'."""
    if not ref or not isinstance(ref, str):
        problems.at(where, "ref is required")
        return
    mod_id, _, qid = ref.rpartition(".")
    target = project.modules.get(mod_id or module_id)
    if target is None:
        problems.at(where, f"ref '{ref}' points to an unknown module")
    elif qid not in target.impl.question_ids():
        problems.at(where, f"ref '{ref}' points to an unknown question")


class Scope:
    """Problems bound to one file, so module types can report without knowing the path."""

    def __init__(self, problems, where):
        self.problems, self.where = problems, where

    def add(self, msg):
        self.problems.at(self.where, msg)

    def ref(self, project, module_id, ref, what):
        check_ref(project, module_id, ref, self.problems, f"{self.where} {what}")


def studio_dir(data_dir=None):
    """Projects created or imported from the admin site live here, next to the databases and
    outside the repository, so real studies are never committed by accident."""
    return Path(data_dir or os.environ.get("DATA_DIR") or "instance").expanduser() / "projects"


def project_dirs(data_dir=None):
    """Read-only folders from PROJECTS_DIRS, then the Studio folder (created if missing)."""
    raw = os.environ.get("PROJECTS_DIRS") or "projects"
    dirs = [Path(p).expanduser() for p in raw.split(os.pathsep) if p]
    studio = studio_dir(data_dir)
    studio.mkdir(parents=True, exist_ok=True)
    return dirs + [studio]


def studio_dir_of(dirs):
    """By convention the last folder in the list is the writable Studio folder."""
    return Path(dirs[-1]).resolve() if dirs else None


def load_all(module_types, dirs=None):
    problems = Problems()
    projects = {}
    dirs = list(dirs or project_dirs())
    for base in dirs:
        if not base.is_dir():
            problems.at(str(base), "projects directory does not exist")
            continue
        for pdir in sorted(p for p in base.iterdir() if (p / "project.yaml").is_file()):
            project = load_project(pdir, module_types, problems)
            if project is None:
                continue
            if project.slug in projects:
                problems.at(str(pdir), f"slug '{project.slug}' already loaded from {projects[project.slug].dir}")
                continue
            project.editable = pdir.parent.resolve() == studio_dir_of(dirs)
            projects[project.slug] = project
    if problems:
        raise ConfigError(problems)
    return projects


class Registry:
    """Holds the loaded projects and reloads them when any YAML file changes, so researchers
    can edit questions without restarting. A broken edit keeps the last good config running."""

    def __init__(self, module_types, dirs):
        self.module_types = module_types
        self.dirs = list(dirs)
        self.projects = load_all(module_types, dirs)
        self.signature = self._signature()
        self.last_error = None

    def _signature(self):
        sig = []
        for base in self.dirs:
            if base.is_dir():
                for path in base.glob("*/**/*.yaml"):
                    try:
                        sig.append((str(path), path.stat().st_mtime_ns))
                    except OSError:
                        pass
                for path in base.glob("*/project.yaml"):
                    sig.append((str(path), path.stat().st_mtime_ns))
        return tuple(sorted(set(sig)))

    @property
    def studio(self):
        return self.dirs[-1]

    def refresh(self, force=False):
        sig = self._signature()
        if sig == self.signature and not force:
            return
        self.signature = sig
        try:
            self.projects = load_all(self.module_types, self.dirs)
            self.last_error = None
        except ConfigError as exc:
            self.last_error = exc
            log.error("%s\nKeeping the previous configuration.", exc)

    def get(self, slug):
        return self.projects.get(slug)
