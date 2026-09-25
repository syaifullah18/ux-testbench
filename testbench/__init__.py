"""UX Testbench: host many user-research projects (surveys, A/B prototype tests) in one app."""
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from . import storage
from .config import Registry
from .modules import MODULE_TYPES

__version__ = "0.2.0"
log = logging.getLogger(__name__)


def create_app(overrides=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "",
        DATA_DIR=os.environ.get("DATA_DIR", str(Path.cwd() / "instance")),
        PROJECTS_DIRS=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        log.warning("SECRET_KEY is not set; using a random key, so sessions reset on every restart.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)

    dirs = app.config["PROJECTS_DIRS"]
    app.extensions["testbench"] = Registry(MODULE_TYPES, [Path(d) for d in dirs] if dirs else None)

    @app.before_request
    def refresh_projects():
        app.extensions["testbench"].refresh()

    app.teardown_appcontext(storage.close_all)
    app.jinja_env.filters["secs"] = fmt_seconds
    app.jinja_env.filters["pct"] = lambda v: "-" if v is None else f"{round(v * 100)}%"
    app.jinja_env.filters["num"] = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"

    from . import admin, web
    app.register_blueprint(admin.bp)   # before web, so /admin/ is never taken as a project slug
    app.register_blueprint(web.bp)
    return app


def fmt_seconds(ms):
    if ms is None:
        return "-"
    s = round(ms / 1000)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"
