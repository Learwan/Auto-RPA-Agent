import pytest

from src.notification.service import (
    EmailConfig,
    Notification,
    NotificationService,
    NotificationType,
    WebhookConfig,
)


class TestNotification:
    def test_creation(self):
        n = Notification(
            notification_type=NotificationType.EXECUTION_COMPLETED,
            title="Test",
            message="Test message",
        )
        assert n.notification_type == NotificationType.EXECUTION_COMPLETED
        assert n.severity == "info"

    def test_custom_severity(self):
        n = Notification(
            notification_type=NotificationType.EXECUTION_FAILED,
            title="Error",
            message="Failed",
            severity="error",
        )
        assert n.severity == "error"


class TestNotificationService:
    def test_singleton(self):
        svc1 = NotificationService.get_instance()
        svc2 = NotificationService.get_instance()
        assert svc1 is svc2

    def test_configure_webhook(self):
        svc = NotificationService()
        config = WebhookConfig(url="https://example.com/webhook", secret="test")
        svc.configure_webhook(config)
        assert len(svc._webhooks) == 1
        assert svc._webhooks[0].url == "https://example.com/webhook"

    def test_configure_webhook_replaces_existing(self):
        svc = NotificationService()
        svc.configure_webhook(WebhookConfig(url="https://example.com/hook"))
        svc.configure_webhook(WebhookConfig(url="https://example.com/hook", secret="new"))
        assert len(svc._webhooks) == 1
        assert svc._webhooks[0].secret == "new"

    def test_configure_email(self):
        svc = NotificationService()
        config = EmailConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            enabled=True,
        )
        svc.configure_email(config)
        assert svc._email_config.smtp_host == "smtp.example.com"
        assert svc._email_config.enabled is True

    @pytest.mark.asyncio
    async def test_notify_adds_to_history(self):
        svc = NotificationService()
        svc.clear_history()
        n = Notification(
            notification_type=NotificationType.EXECUTION_STARTED,
            title="Started",
            message="Execution started",
        )
        await svc.notify(n)
        assert len(svc.history) == 1
        assert svc.history[0].title == "Started"

    @pytest.mark.asyncio
    async def test_history_truncation(self):
        svc = NotificationService()
        svc.clear_history()
        for i in range(1001):
            n = Notification(
                notification_type=NotificationType.STEP_FAILED,
                title=f"Step {i}",
                message="Failed",
            )
            await svc.notify(n)
        assert len(svc.history) <= 500

    def test_clear_history(self):
        svc = NotificationService()
        svc._history = [
            Notification(
                notification_type=NotificationType.SYSTEM_ERROR,
                title="Test",
                message="Test",
            )
        ]
        svc.clear_history()
        assert len(svc.history) == 0


class TestWebhookConfig:
    def test_default_values(self):
        config = WebhookConfig(url="https://example.com")
        assert config.enabled is True
        assert config.secret == ""
        assert config.events == []

    def test_event_filtering(self):
        config = WebhookConfig(
            url="https://example.com",
            events=[NotificationType.EXECUTION_COMPLETED],
        )
        assert len(config.events) == 1


class TestEmailConfig:
    def test_default_values(self):
        config = EmailConfig()
        assert config.enabled is False
        assert config.smtp_port == 587
        assert config.use_tls is True
