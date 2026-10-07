from __future__ import annotations

import logging
import os
import smtplib
from concurrent.futures import Future, ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

_MAIL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chef-mail")


def _get_mail_logger() -> logging.Logger:
    """Return a dedicated logger that persists asynchronous mail errors to file."""
    logger = logging.getLogger("chef.mail")
    if logger.handlers:
        return logger

    logger.setLevel(logging.ERROR)
    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "mail_errors.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def run_async(func: F) -> Callable[..., Future[Any]]:
    """Decorator that executes a function in a shared background thread pool."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Future[Any]:
        try:
            return _MAIL_EXECUTOR.submit(func, *args, **kwargs)
        except Exception:
            _get_mail_logger().error(
                "Failed to schedule async task %s",
                getattr(func, "__name__", "unknown"),
                exc_info=True,
            )
            raise

    return wrapper


def build_recovery_email_message(sender: str, destinatario: str, enlace: str) -> MIMEMultipart:
    """Build a MIME message for password recovery."""
    subject = "Recupera tu acceso a jemPOS Chef"
    cuerpo_texto = (
        "Hola, recibimos una solicitud para cambiar tu contraseña en jemPOS Chef.\n"
        "Haz clic en el enlace para continuar:\n\n"
        f"{enlace}\n\n"
        "El enlace es válido por 30 minutos.\n"
        "Si no fuiste tú, ignora este mensaje.\n"
    )
    cuerpo_html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; background-color: #f8fafc; color: #0f172a; padding: 24px;">
        <div style="max-width: 520px; margin: 0 auto; background: #ffffff; border: 1px solid #e2e8f0;
                    border-radius: 16px; padding: 24px;">
          <h2 style="margin: 0 0 12px; color: #0f172a;">Recupera tu acceso a jemPOS Chef</h2>
          <p style="margin: 0 0 16px; color: #475569; line-height: 1.5;">
            Hola, recibimos una solicitud para cambiar tu contraseña en jemPOS Chef.
            Haz clic en el enlace para continuar:
          </p>
          <div style="text-align: center; margin: 24px 0;">
            <a href="{enlace}"
               style="background: #c2410c; color: #ffffff; text-decoration: none; padding: 12px 24px;
                      border-radius: 12px; display: inline-block; font-weight: 600;">
              Recuperar acceso
            </a>
          </div>
          <p style="margin: 0 0 8px; color: #475569; line-height: 1.5;">
            El enlace es válido por 30 minutos.
          </p>
          <p style="margin: 0; color: #94a3b8; font-size: 13px;">
            Si no fuiste tú, ignora este mensaje.
          </p>
        </div>
      </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = destinatario
    msg.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
    msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
    return msg


def _enviar_smtp(destinatario: str, construir: Callable[[str], MIMEMultipart]) -> None:
    """Send the message built by `construir(sender)` using SMTP env configuration."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    smtp_host = os.getenv("EMAIL_SMTP_HOST")
    smtp_port_raw = os.getenv("EMAIL_SMTP_PORT")

    if not sender or not password or not smtp_host or not smtp_port_raw:
        raise ValueError("Missing SMTP environment configuration")

    smtp_port_str = smtp_port_raw.strip()
    if not smtp_port_str.isdigit():
        raise ValueError("EMAIL_SMTP_PORT must be numeric")

    smtp_port = int(smtp_port_str)
    msg = construir(sender)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(sender, password)
        smtp.sendmail(sender, [destinatario], msg.as_string())


def send_recovery_email_sync(destinatario: str, enlace: str) -> None:
    """Send password recovery email synchronously using SMTP env configuration."""
    _enviar_smtp(
        destinatario,
        lambda sender: build_recovery_email_message(sender=sender, destinatario=destinatario, enlace=enlace),
    )


@run_async
def send_recovery_email_async(destinatario: str, enlace: str) -> None:
    """Fire-and-forget recovery email sender.

    Any SMTP or configuration failure is logged and never propagated
    to the HTTP request thread.
    """
    try:
        send_recovery_email_sync(destinatario=destinatario, enlace=enlace)
    except Exception:
        _get_mail_logger().error(
            "Failed to send recovery email to %s",
            destinatario,
            exc_info=True,
        )
