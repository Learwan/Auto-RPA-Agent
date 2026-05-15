from __future__ import annotations

import json
import logging
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)


class NotificationType(StrEnum):
    EXECUTION_STARTED = "execution_started"
    EXECUTION_COMPLETED = "execution_completed"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_PAUSED = "execution_paused"
    STEP_FAILED = "step_failed"
    SCHEDULE_TRIGGERED = "schedule_triggered"
    CREDENTIAL_EXPIRING = "credential_expiring"
    SYSTEM_ERROR = "system_error"


@dataclass
class Notification:
    notification_type: NotificationType
    title: str
    message: str
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0
    severity: str = "info"


@dataclass
class WebhookConfig:
    url: str
    secret: str = ""
    headers: dict = field(default_factory=dict)
    enabled: bool = True
    events: list[NotificationType] = field(default_factory=list)


@dataclass
class EmailConfig:
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    to_addresses: list[str] = field(default_factory=list)
    use_tls: bool = True
    enabled: bool = False
    events: list[NotificationType] = field(default_factory=list)


class NotificationService:
    _instance: NotificationService | None = None

    @classmethod
    def get_instance(cls) -> NotificationService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._webhooks: list[WebhookConfig] = []
        self._email_config = EmailConfig()
        self._history: list[Notification] = []

    def configure_webhook(self, config: WebhookConfig) -> None:
        self._webhooks = [w for w in self._webhooks if w.url != config.url]
        self._webhooks.append(config)

    def configure_email(self, config: EmailConfig) -> None:
        self._email_config = config

    async def notify(self, notification: Notification) -> None:
        import time

        notification.timestamp = time.time()
        self._history.append(notification)
        if len(self._history) > 1000:
            self._history = self._history[-500:]

        await self._send_webhooks(notification)
        await self._send_email(notification)

    async def _send_webhooks(self, notification: Notification) -> None:
        for webhook in self._webhooks:
            if not webhook.enabled:
                continue
            if webhook.events and notification.notification_type not in webhook.events:
                continue
            try:
                await self._post_webhook(webhook, notification)
            except Exception as e:
                logger.warning("Webhook delivery failed for %s: %s", webhook.url, e)

    async def _post_webhook(self, webhook: WebhookConfig, notification: Notification) -> None:
        payload = {
            "type": notification.notification_type.value,
            "title": notification.title,
            "message": notification.message,
            "severity": notification.severity,
            "data": notification.data,
            "timestamp": notification.timestamp,
        }

        headers = {"Content-Type": "application/json"}
        if webhook.secret:
            headers["X-Webhook-Secret"] = webhook.secret
        headers.update(webhook.headers)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook.url, json=payload, headers=headers)
            if response.status_code >= 400:
                logger.warning(
                    "Webhook returned %d for %s",
                    response.status_code,
                    webhook.url,
                )

    async def _send_email(self, notification: Notification) -> None:
        config = self._email_config
        if not config.enabled:
            return
        if not config.smtp_host or not config.to_addresses:
            return
        if config.events and notification.notification_type not in config.events:
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Auto-Agent] {notification.title}"
            msg["From"] = config.from_address or config.smtp_user
            msg["To"] = ", ".join(config.to_addresses)

            text_body = (
                f"{notification.title}\n\n"
                f"{notification.message}\n\n"
                f"Type: {notification.notification_type.value}\n"
                f"Severity: {notification.severity}\n"
            )
            if notification.data:
                text_body += f"\nDetails:\n{json.dumps(notification.data, indent=2, default=str)}"

            html_body = self._build_html_email(notification)
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            if config.use_tls:
                server = smtplib.SMTP(config.smtp_host, config.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(config.smtp_host, config.smtp_port)

            if config.smtp_user and config.smtp_password:
                server.login(config.smtp_user, config.smtp_password)

            server.sendmail(
                config.from_address or config.smtp_user,
                config.to_addresses,
                msg.as_string(),
            )
            server.quit()
            logger.info("Email notification sent: %s", notification.title)
        except Exception as e:
            logger.warning("Email notification failed: %s", e)

    def _build_html_email(self, notification: Notification) -> str:
        severity_colors = {
            "info": "#2196F3",
            "warning": "#FF9800",
            "error": "#F44336",
            "success": "#4CAF50",
        }
        color = severity_colors.get(notification.severity, "#2196F3")
        data_rows = ""
        if notification.data:
            for key, value in notification.data.items():
                data_rows += f"<tr><td>{key}</td><td>{value}</td></tr>"

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; margin: 20px;">
            <div style="border-left: 4px solid {color}; padding: 12px 20px; background: #f9f9f9;">
                <h2 style="margin: 0; color: {color};">{notification.title}</h2>
                <p style="margin: 8px 0;">{notification.message}</p>
                <p style="margin: 4px 0; color: #666;">
                    Type: {notification.notification_type.value} |
                    Severity: {notification.severity}
                </p>
            </div>
            {"<table style='margin-top: 12px; border-collapse: collapse;'>" + data_rows + "</table>" if data_rows else ""}
        </body>
        </html>
        """

    @property
    def history(self) -> list[Notification]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history = []
