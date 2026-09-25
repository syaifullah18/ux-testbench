"""UX Testbench: host many user-research projects (surveys, A/B prototype tests) in one app."""
import hmac
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, abort, g, render_template, request, session

from . import passcodes, storage, users
from .config import Registry, project_dirs, studio_dir
from .modules import MODULE_TYPES

__version__ = "0.3.0"
log = logging.getLogger(__name__)


load_dotenv()


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or "",
        DATA_DIR=os.environ.get("DATA_DIR", str(Path.cwd() / "instance")),
        PROJECTS_DIRS=None,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", 50)) * 1024 * 1024,
        TESTBENCH_MODE=os.environ.get("TESTBENCH_MODE", "internal"),
        LOCAL_ASSETS=os.environ.get("LOCAL_ASSETS") == "1",
    )
    app.config.update(overrides or {})
    if app.config["TESTBENCH_MODE"] == "public" and not app.config.get("TESTING"):
        # A public instance is served over HTTPS, so the session cookie says so.
        app.config["SESSION_COOKIE_SECURE"] = True
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
    app.teardown_appcontext(users.close_users_db)
    app.jinja_env.filters["secs"] = fmt_seconds
    app.jinja_env.filters["pct"] = lambda v: "-" if v is None else f"{round(v * 100)}%"
    app.jinja_env.filters["num"] = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"

    @app.before_request
    def assign_request_id():
        """One id per request, echoed in the response and in every log line, so a report of
        "it broke at 14:02" can be traced to the exact request."""
        g.request_id = request.headers.get("X-Request-ID", secrets.token_hex(8))[:64]

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Request-ID", g.get("request_id", ""))
        resp.headers.setdefault("Content-Security-Policy", csp(app))
        return resp

    @app.errorhandler(403)
    def err_403(exc):
        return render_error(403, "Not allowed", "You do not have access to this page."), 403

    @app.errorhandler(404)
    def err_404(exc):
        return render_error(404, "Page not found",
                            "The link may be mistyped, or the study may have been removed."), 404

    @app.errorhandler(413)
    def err_413(exc):
        mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return render_error(413, "That upload is too large",
                            f"The server accepts at most {mb} MB in one request."), 413

    @app.errorhandler(500)
    def err_500(exc):
        log.exception("Unhandled error on %s (request %s)", request.path, g.get("request_id", "-"))
        return render_error(500, "Something went wrong on our side",
                            "The error has been logged. Please try again in a moment."), 500

    @app.before_request
    def ensure_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)

    @app.before_request
    def csrf_protect():
        from flask import current_app
        if current_app.testing:
            return
        if request.method == "POST":
            token = session.get("csrf_token")
            if not token:
                abort(403, "Missing CSRF token")
            given = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
            if not given or not hmac.compare_digest(given, token):
                abort(403, "Invalid CSRF token")

    @app.context_processor
    def inject_csrf():
        return {"csrf_token": session.get("csrf_token", "")}

    from . import admin, auth, platform_admin, public, studio, web
    if os.environ.get("TESTBENCH_MODE", "internal") == "public":
        app.register_blueprint(auth.bp)
        app.register_blueprint(platform_admin.bp)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)   # before web, so /admin/ is never taken as a project slug
    app.register_blueprint(studio.bp)
    app.register_blueprint(web.bp)
    return app


def csp(app):
    """A Content-Security-Policy that matches how the pages are actually built.

    The participant and admin pages load Tailwind, fonts and icons from CDNs unless
    LOCAL_ASSETS=1, and Tailwind's browser build needs inline script and style. Prototypes are a
    separate matter: they are researcher-supplied HTML, and moving them to their own origin is
    what contains them, not this header.
    """
    script = "'self' 'unsafe-inline' 'unsafe-eval'"
    style = "'self' 'unsafe-inline'"
    font = "'self' data:"
    if not app.config.get("LOCAL_ASSETS"):
        script += " https://cdn.tailwindcss.com"
        style += " https://fonts.googleapis.com https://cdnjs.cloudflare.com"
        font += " https://fonts.gstatic.com https://cdnjs.cloudflare.com"
    return "; ".join([
        "default-src 'self'",
        f"script-src {script}",
        f"style-src {style}",
        f"font-src {font}",
        "img-src 'self' data: blob:",
        "frame-ancestors 'self'",
        "base-uri 'none'",
        "form-action 'self'",
    ])


def render_error(code, title, message):
    try:
        return render_template("error.html", code=code, title=title, message=message,
                               request_id=g.get("request_id", ""), project=None)
    except Exception:       # a template failure must not replace the real error
        return f"{code} {title}: {message}"


def fmt_seconds(ms):
    if ms is None:
        return "-"
    s = round(ms / 1000)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"
