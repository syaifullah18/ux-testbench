"""Command line: python -m testbench [run|check|new|demo|create-admin|claim|retention]."""
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


def cmd_demo(args):
    from .demo import seed_example
    seed_example(args.n)
    return 0


def cmd_retention(args):
    """Housekeeping a public instance should run nightly: clear old IP addresses, drop spent
    one-time tokens, and warn or delete studies that closed long ago."""
    from . import create_app, moderation
    app = create_app()
    with app.app_context():
        stats = moderation.prune_ips(days=args.ip_days)
        stats["tokens_deleted"] = moderation.prune_tokens()
        stats["rate_events_deleted"] = moderation.prune_rate_events()
        for key, value in stats.items():
            print(f"{key:<26} {value}")
        if args.close_after:
            from .retention import stale_closed_projects
            stale = stale_closed_projects(app, months=args.close_after)
            if not stale:
                print("no closed studies past the retention window")
            for slug, closed_since in stale:
                print(f"closed study past retention: {slug} (last activity {closed_since})")
            print("\nNothing was deleted. Review the list, tell the owners, then delete by hand.")
    return 0


def cmd_run(args):
    from . import create_app
    create_app().run(host=args.host, port=args.port, debug=args.debug)
    return 0


def cmd_create_admin(args):
    from . import create_app, users
    import getpass
    app = create_app()
    with app.app_context():
        email = args.email.strip().lower()
        if not email or "@" not in email:
            print("Invalid email.", file=sys.stderr)
            return 1
        existing = users.get_user_by_email(email)
        if existing:
            if not existing["is_platform_admin"]:
                users.update_user(existing["id"], is_platform_admin=1)
                print(f"Granted platform admin to existing user {email}")
            else:
                print(f"{email} is already a platform admin.")
            return 0
        
        while True:
            pw = getpass.getpass("Password (min 10 chars): ")
            if len(pw) >= 10:
                break
            print("Password too short.")
        
        user_id, problems = users.create_user(email, "Admin", pw)
        if problems:
            print(f"Failed: {problems}", file=sys.stderr)
            return 1
        users.update_user(user_id, is_platform_admin=1)
        users.set_email_verified(user_id)
        print(f"Platform admin {email} created.")
        return 0


def cmd_claim(args):
    from . import create_app, users
    from .config import ID_RE
    app = create_app()
    with app.app_context():
        if not ID_RE.match(args.slug):
            print("Invalid project slug.", file=sys.stderr)
            return 1
        email = args.email.strip().lower()
        user = users.get_user_by_email(email)
        if not user:
            print(f"User {email} not found. They must sign up first.", file=sys.stderr)
            return 1
        users.add_membership(args.slug, user["id"], "owner")
        print(f"{email} is now the owner of {args.slug}")
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
    d = sub.add_parser("demo", help="seed synthetic participants into the example project")
    d.add_argument("-n", type=int, default=30, help="number of participants (default 30)")
    
    ca = sub.add_parser("create-admin", help="create a platform admin (public mode)")
    ca.add_argument("email")
    
    cl = sub.add_parser("claim", help="assign an owner to a project (public mode)")
    cl.add_argument("slug")
    cl.add_argument("email")

    rt = sub.add_parser("retention", help="clear old IPs and spent tokens; list stale studies")
    rt.add_argument("--ip-days", type=int, default=30,
                    help="clear IP addresses older than this many days (default 30)")
    rt.add_argument("--close-after", type=int, default=12,
                    help="report closed studies with no activity for this many months (default 12)")
    
    args = parser.parse_args(argv)
    cmds = {"run": cmd_run, "check": cmd_check, "new": cmd_new, "demo": cmd_demo,
            "create-admin": cmd_create_admin, "claim": cmd_claim, "retention": cmd_retention}
    return cmds[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
