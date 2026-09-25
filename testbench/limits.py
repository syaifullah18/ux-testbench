"""Quotas for the public service.

In internal mode nothing here applies: a single operator runs the instance and needs no
limits against themselves. In public mode anyone can sign up, so each limit exists to keep one
account from spending the whole instance's disk, database or reputation.

Every limit is an environment variable, so an operator can raise or lower it without a code
change. `limit("projects_per_user")` returns the effective value, and 0 means "no limit".
"""
import os

DEFAULTS = {
    "projects_per_user": 10,          # projects a non-admin account may own
    "participants_per_project": 2000,  # refuses new participants past this, results stay readable
    "signups_per_ip_per_day": 5,
    "upload_mb": 10,                  # one uploaded file
    "project_mb": 100,                # all files of one project
    "modules_per_project": 30,
}

ENV_NAMES = {name: f"LIMIT_{name.upper()}" for name in DEFAULTS}


def is_public_mode():
    return os.environ.get("TESTBENCH_MODE", "internal") == "public"


def limit(name):
    """The effective limit, or 0 for unlimited. Internal mode is always unlimited."""
    if not is_public_mode():
        return 0
    raw = os.environ.get(ENV_NAMES[name])
    if raw is None:
        return DEFAULTS[name]
    try:
        value = int(raw)
    except ValueError:
        return DEFAULTS[name]
    return max(0, value)


def all_limits():
    """For the platform admin screen: {name: effective value}."""
    return {name: limit(name) for name in DEFAULTS}


# ---------------------------------------------------------------- checks
# Each check returns None when the action is allowed, or a message to show the user.


def check_signup(ip):
    from . import moderation
    cap = limit("signups_per_ip_per_day")
    if not cap or ip is None:
        return None
    if moderation.signups_since(ip, hours=24) >= cap:
        return ("This network has reached the daily sign-up limit. "
                "Try again tomorrow, or contact us if you need an account now.")
    return None


def owned_projects(user_id):
    from . import users
    return sum(1 for m in users.user_projects(user_id) if m["role"] == "owner")


def check_new_project(user):
    """Gate on a verified email and the per-account project quota."""
    if not is_public_mode() or user["is_platform_admin"]:
        return None
    if not user["email_verified_at"]:
        return "Please verify your email address before creating a project. Check your inbox."
    cap = limit("projects_per_user")
    if cap and owned_projects(user["id"]) >= cap:
        return (f"You already own {cap} projects, which is the limit for one account. "
                "Delete or close a project, or ask us to raise the limit.")
    return None


def participant_count(slug):
    from . import storage
    try:
        return storage.connect(slug).execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    except Exception:
        return 0


def project_full(slug):
    """True when the study has reached its participant cap. Existing participants can still
    finish; only new ones are turned away."""
    cap = limit("participants_per_project")
    return bool(cap) and participant_count(slug) >= cap


def dir_bytes(path):
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def check_upload(project_dir, incoming_bytes):
    mb = 1024 * 1024
    per_file = limit("upload_mb")
    if per_file and incoming_bytes > per_file * mb:
        return f"That file is larger than the {per_file} MB limit for one upload."
    per_project = limit("project_mb")
    if per_project and dir_bytes(project_dir) + incoming_bytes > per_project * mb:
        return (f"This project would go over its {per_project} MB storage limit. "
                "Delete some files first.")
    return None
