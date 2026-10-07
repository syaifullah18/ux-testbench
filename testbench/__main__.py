"""Command line: python -m testbench [run|check|new|demo|create-admin|claim|delete-user|retention]."""
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
    from . import preflight
    blocking = 0
    for p in projects.values():
        found = preflight.run(p, os.environ.get("LOCAL_ASSETS") == "1")
        for f in found:
            where = f"{p.slug}/{f['file']}" + (f" [{f['module']}:{f['variant']}]" if f.get("variant") else "")
            print(f"  {f['severity'].upper():<7} {where}: {f['message']}. Fix: {f['fix']}")
        blocking += preflight.summary(found)[0]
    locked = lock_problems(projects)
    if locked:
        print("Locked analysis rules were changed:\n  - " + "\n  - ".join(locked), file=sys.stderr)
        return 1
    if blocking:
        print(f"Preflight: {blocking} error(s) would block going live.", file=sys.stderr)
        return 1
    print(f"OK: {len(projects)} project(s)")
    return 0


def lock_problems(projects):
    """Every change to rules locked when a study went live (see locks.py)."""
    from . import create_app, db, locks, storage
    app = create_app()
    out = []
    with app.app_context():
        try:
            for p in projects.values():
                if db.study_exists(p.slug):
                    out += [f"{p.slug}/{msg}" for msg in locks.violations(storage.connect(p.slug), p)]
        except Exception as exc:   # the YAML check must still work when the database is unreachable
            print(f"warning: locked rules not checked, database unavailable ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
        finally:
            storage.close_all()
    return out


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


def cmd_migrate_to_postgres(args):
    """Copy every SQLite database in DATA_DIR into the PostgreSQL database in DATABASE_URL."""
    from . import create_app, migrate
    app = create_app()
    with app.app_context():
        results = migrate.sqlite_to_postgres(args.data_dir or app.config["DATA_DIR"], dry_run=args.dry_run)
    failed = [k for k, v in results.items() if v.startswith("failed")]
    if failed:
        print(f"{len(failed)} database(s) failed: {', '.join(failed)}. The SQLite files are untouched.")
        return 1
    if not args.dry_run and results:
        print("Done. Check the app, then keep the SQLite files as a backup until you are sure.")
    return 0


def cmd_migrate_files_to_s3(_args):
    """Upload every Studio study folder to the bucket, then check each object against the disk."""
    from pathlib import Path
    from . import create_app, filestore
    from .config import studio_dir
    app = create_app()
    if not filestore.enabled(app):
        print("Set STORAGE_BACKEND=s3 and the S3_* settings first.")
        return 1
    with app.app_context():
        studio = studio_dir(app.config["DATA_DIR"])
        d0, u0 = app.extensions.get("tb_restored", (0, 0))   # create_app() already ran one pass
        down, up = filestore.restore(studio)
        down, up = down + d0, up + u0
        st = filestore.store()
        remote = st.list(f"{st.prefix}/studies/")
        missing = [f"{s.name}/{rel}" for s in Path(studio).iterdir() if s.is_dir() and not s.name.startswith(".")
                   for rel, path in filestore._local_files(s).items()
                   if remote.get(f"{st.prefix}/studies/{s.name}/{rel}") != filestore._md5(path)]
    print(f"Uploaded {up} file(s); downloaded {down} the disk was missing. {len(remote)} object(s) in the bucket.")
    if missing:
        print(f"{len(missing)} file(s) do not match the bucket: " + ", ".join(missing[:10]))
        return 1
    print("Every study file is in the bucket and matches the disk. The local copies are kept.")
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

        from .auth import password_login_enabled
        if not password_login_enabled():
            user_id, problems = users.create_user(email, "Admin")
            if problems:
                print(f"Failed: {problems}", file=sys.stderr)
                return 1
            users.update_user(user_id, is_platform_admin=1)
            users.set_email_verified(user_id)
            print(f"Platform admin {email} created. Sign in with Google or GitHub using this address.")
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


def cmd_delete_user(args):
    """Remove an account the same way the user would from /account, or only disable it."""
    from . import create_app, moderation, storage, users
    from .auth import _release_projects, sole_owned
    app = create_app()
    with app.app_context():
        email = args.email.strip().lower()
        user = users.get_user_by_email(email)
        if not user:
            print(f"User {email} not found.", file=sys.stderr)
            return 1
        if user["is_platform_admin"] and not any(
                u["is_platform_admin"] and not u["disabled_at"] and u["id"] != user["id"]
                for u in users.list_users()):
            print(f"{email} is the last platform admin. Create another with create-admin first.",
                  file=sys.stderr)
            return 1

        if args.disable:
            users.update_user(user["id"], disabled_at=storage.now_iso())
            users.revoke_all_sessions(user["id"])
            moderation.audit("user.disable", actor="cli", detail={"email": email})
            print(f"{email} disabled and signed out everywhere.")
            return 0

        orphans = sole_owned(user)
        if orphans and not args.delete_studies:
            print(f"{email} is the only owner of: {', '.join(orphans)}.\n"
                  f"Make someone else an owner (claim <slug> <email>), or pass --delete-studies "
                  f"to delete those studies and their participant data too.", file=sys.stderr)
            return 1
        if not args.yes:
            warning = f" and the studies {', '.join(orphans)}" if orphans else ""
            if input(f"Permanently delete {email}{warning}? Type the email to confirm: ").strip().lower() != email:
                print("Cancelled.")
                return 1

        _release_projects(user, "delete" if args.delete_studies else "keep")
        users.revoke_all_sessions(user["id"])
        moderation.audit("account.delete", actor="cli", detail={"email": email})
        moderation.anonymise_user(user["id"])
        users.delete_user(user["id"])   # memberships, sessions and tokens cascade
        print(f"{email} deleted.")
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

    du = sub.add_parser("delete-user", help="delete (or --disable) a researcher account (public mode)")
    du.add_argument("email")
    du.add_argument("--disable", action="store_true",
                    help="only disable the account and sign it out; nothing is deleted")
    du.add_argument("--delete-studies", action="store_true",
                    help="also delete studies this user owns alone, with their participant data")
    du.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    rt = sub.add_parser("retention", help="clear old IPs and spent tokens; list stale studies")
    rt.add_argument("--ip-days", type=int, default=30,
                    help="clear IP addresses older than this many days (default 30)")
    rt.add_argument("--close-after", type=int, default=12,
                    help="report closed studies with no activity for this many months (default 12)")
    
    mg = sub.add_parser("migrate-to-postgres",
                        help="copy every SQLite database in DATA_DIR into DATABASE_URL (PostgreSQL)")
    mg.add_argument("--data-dir", help="where the .db files are (default: DATA_DIR)")
    mg.add_argument("--dry-run", action="store_true", help="list what would be copied, write nothing")

    sub.add_parser("migrate-files-to-s3",
                   help="upload every Studio study folder to the bucket and verify it (STORAGE_BACKEND=s3)")

    args = parser.parse_args(argv)
    cmds = {"run": cmd_run, "check": cmd_check, "new": cmd_new, "demo": cmd_demo,
            "create-admin": cmd_create_admin, "claim": cmd_claim, "delete-user": cmd_delete_user,
            "retention": cmd_retention,
            "migrate-to-postgres": cmd_migrate_to_postgres,
            "migrate-files-to-s3": cmd_migrate_files_to_s3}
    return cmds[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
