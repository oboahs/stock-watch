from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from utils import LOGGER, env_bool


def send_email_report(subject: str, body: str, attachment_path: Path | None = None) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("SMTP_FROM", user).strip()
    receiver = os.getenv("SMTP_TO", "").strip()
    use_ssl = env_bool("SMTP_USE_SSL", True)

    if not all([host, user, password, sender, receiver]):
        LOGGER.info("email not sent because SMTP env vars are incomplete")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = receiver
    message.set_content(body)

    if attachment_path and attachment_path.exists():
        message.add_attachment(
            attachment_path.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=attachment_path.name,
        )

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(message)
        LOGGER.info("email report sent to %s", receiver)
        return True
    except Exception as exc:
        LOGGER.exception("email report failed: %s", exc)
        return False

