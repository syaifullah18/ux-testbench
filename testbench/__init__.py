"""UX Testbench: host many user-research projects (surveys, A/B prototype tests) in one app."""
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from . import passcodes, storage
from .config import Registry, project_dirs, studio_dir
from .modules import MODULE_TYPES

__version__ = "0.3.0"
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
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", 50)) * 1024 * 1024,
    )
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        log.warning("SECRET_KEY is not set; using a random key, so sessions reset on every restart.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)

    data_dir = app.config["DATA_DIR"]
    if app.config["PROJECTS_DIRS"] is None:
        dirs = project_dirs(data_dir)
    else:
        studio = studio_dir(data_dir)
        studio.mkdir(parents=True, exist_ok=True)
        dirs = [Path(d) for d in app.config["PROJECTS_DIRS"]] + [studio]
    app.extensions["testbench"] = Registry(MODULE_TYPES, dirs)

    @app.before_request
    def refresh_projects():
        app.extensions["testbench"].refresh()

    app.teardown_appcontext(storage.close_all)
    app.teardown_appcontext(passcodes.close)
    app.jinja_env.filters["secs"] = fmt_seconds
    app.jinja_env.filters["pct"] = lambda v: "-" if v is None else f"{round(v * 100)}%"
    app.jinja_env.filters["num"] = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"

    from . import admin, studio, web
    app.register_blueprint(admin.bp)   # before web, so /admin/ is never taken as a project slug
    app.register_blueprint(studio.bp)
    app.register_blueprint(web.bp)
    return app


def fmt_seconds(ms):
    if ms is None:
        return "-"
    s = round(ms / 1000)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"
