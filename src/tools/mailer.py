"""Outbound mail, over stdlib smtplib.

One caller today: the password-reset link. Config is the `smtp` block in settings.json
(host, port, username, password, from_address); an unconfigured block means the platform
cannot send, and `configured()` is what the reset endpoint checks so it can refuse honestly
instead of promising mail that never arrives.

A boundary like snapshots and archives: a send that fails is logged and raised to the caller,
never retried blind — a queued retry would mean storing the message, and a reset link is the
one thing that should not sit on disk.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Dict

from config.settings_manager import settings_manager

logger = logging.getLogger(__name__)


def _config() -> Dict:
    return settings_manager.get_settings().get("smtp") or {}


def configured() -> bool:
    c = _config()
    return bool(c.get("host") and c.get("from_address"))


def send(to: str, subject: str, body: str) -> None:
    """Deliver one plain-text message. Raises if the platform cannot send it."""
    c = _config()
    if not configured():
        raise RuntimeError("no smtp configuration")
    msg = EmailMessage()
    msg["From"] = c["from_address"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    port = int(c.get("port", 587))
    context = ssl.create_default_context()
    # 465 is implicit TLS, 587 is STARTTLS — the port decides, since a server offering one
    # refuses the other outright.
    if port == 465:
        server = smtplib.SMTP_SSL(c["host"], port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(c["host"], port, timeout=20)
    with server:
        if port != 465:
            server.starttls(context=context)
        if c.get("username"):
            server.login(c["username"], c.get("password", ""))
        server.send_message(msg)
    logger.info("sent %r to %s", subject, to)
