"""Command line: python -m testbench [run|check|new]."""
import argparse
import os
import shutil
import sys
from pathlib import Path

from .config import ConfigError, SLUG_RE, RESERVED_SLUGS, env_prefix, load_all, project_dirs, studio_dir
from .modules import MODULE_TYPES

SCAFFOLD = Path(__file__).parent / "scaffold"


def cmd_check(_args):
    try:
        projects = load_all(MODULE_TYPES)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    for p in projects.values():
        print(f"{p.slug:<16} {p.name}  [{p.identity['mode']} login, locale {p.locale}]")
        for m in p.modules.values():
            extra = f"  requires {', '.join(m.requires)}" if m.requires else ""
            print(f"    - {m.id:<20} {m.type:<8} {m.title}{extra}")
        where = "studio, editable" if p.editable else "read-only"
        print(f"    ({where}: {p.dir})")
    print(f"OK: {len(projects)} project(s)")
    return 0


def cmd_new(args):
    slug = args.slug
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        print(f"Invalid slug '{slug}': use lowercase letters, digits and hyphens, and not one of {sorted(RESERVED_SLUGS)}.", file=sys.stderr)
        return 1
    if any((d / slug).exists() for d in project_dirs()):
        print(f"A project named '{slug}' already exists.", file=sys.stderr)
        return 1
    target = studio_dir() / slug  # the Studio folder: git-ignored and editable from the admin site
    shutil.copytree(SCAFFOLD, target)
    yaml_path = target / "project.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8").replace("__NAME__", args.name or slug), encoding="utf-8")
    prefix = env_prefix(slug)
    print(f"Created {target}\nSet passcodes in the Studio (/{slug}/admin/studio/) or with "
          f"{prefix}_PASSCODE and {prefix}_ADMIN_PASSCODE, then open /{slug}/")
    return 0


def cmd_run(args):
    from . import create_app
    create_app().run(host=args.host, port=args.port, debug=args.debug)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m testbench")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start the development server")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    r.add_argument("--debug", action="store_true")
    sub.add_parser("check", help="validate every project's YAML")
    n = sub.add_parser("new", help="create a project from the scaffold")
    n.add_argument("slug")
    n.add_argument("--name")
    args = parser.parse_args(argv)
    return {"run": cmd_run, "check": cmd_check, "new": cmd_new}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
