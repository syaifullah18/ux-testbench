"""Email sending. Uses stdlib smtplib with SMTP_URL from environment.

When SMTP_URL is unset (development), emails are printed to the log.
SMTP_URL format: smtp://user:pass@host:port or smtps://user:pass@host:587
"""
import logging
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse

from flask import current_app, render_template

log = logging.getLogger(__name__)


def _smtp_config():
    url = os.environ.get("SMTP_URL", "")
    if not url:
        return None
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 587,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "tls": parsed.scheme in ("smtps", "smtp+tls"),
    }


def _mail_from():
    return os.environ.get("MAIL_FROM", "noreply@testbench.local")


def send(to, subject, body_text, body_html=None):
    """Send an email. In dev (no SMTP_URL) prints to log instead."""
    msg = EmailMessage()
    msg["From"] = _mail_from()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    cfg = _smtp_config()
    if cfg is None:
        # No SMTP configured, so this is development. The message goes to stdout rather than
        # through the logger: nothing configures logging at INFO by default, and a verification
        # or invitation link that disappears makes the flow impossible to try locally.
        banner = "─" * 68
        print(f"\n{banner}\n  EMAIL (development: not sent, no SMTP_URL is set)\n{banner}\n"
              f"  To:      {to}\n  Subject: {subject}\n{banner}\n{body_text}\n{banner}\n",
              flush=True)
        return

    try:
        if cfg["tls"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
            server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
        server.quit()
    except Exception:
        log.exception("Failed to send email to %s", to)


def send_verification(email, token, base_url):
    link = f"{base_url}/verify/{token}"
    send(
        to=email,
        subject="Verify your email — UX Testbench",
        body_text=f"Click the link to verify your email:\n\n{link}\n\nThis link expires in 24 hours.",
    )


def send_password_reset(email, token, base_url):
    link = f"{base_url}/reset/{token}"
    send(
        to=email,
        subject="Reset your password — UX Testbench",
        body_text=f"Click the link to reset your password:\n\n{link}\n\nThis link expires in 1 hour. If you did not request this, ignore this email.",
    )


def send_invitation(email, token, base_url, project_name, inviter, role):
    link = f"{base_url}/invite/{token}"
    send(
        to=email,
        subject=f"{inviter} invited you to “{project_name}” — UX Testbench",
        body_text=(
            f"{inviter} has invited you to work on the study “{project_name}” as a {role}.\n\n"
            f"Open this link to accept:\n\n{link}\n\n"
            "If you do not have an account yet, the link will offer to create one with this "
            "email address. The invitation expires in 7 days.\n\n"
            "If you were not expecting this, you can ignore this email."
        ),
    )
