from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.notification.service import (
    EmailConfig,
    Notification,
    NotificationService,
    NotificationType,
    WebhookConfig,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class WebhookConfigRequest(BaseModel):
    url: str = Field(..., min_length=1)
    secret: str = Field(default="")
    headers: dict = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    events: list[str] = Field(default_factory=list)


class EmailConfigRequest(BaseModel):
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    from_address: str = Field(default="")
    to_addresses: list[str] = Field(default_factory=list)
    use_tls: bool = Field(default=True)
    enabled: bool = Field(default=False)
    events: list[str] = Field(default_factory=list)


class SendNotificationRequest(BaseModel):
    notification_type: NotificationType
    title: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    data: dict = Field(default_factory=dict)
    severity: str = Field(default="info")


def _get_service() -> NotificationService:
    return NotificationService.get_instance()


@router.get("/history")
async def get_notification_history():
    svc = _get_service()
    return [
        {
            "type": n.notification_type.value,
            "title": n.title,
            "message": n.message,
            "severity": n.severity,
            "timestamp": n.timestamp,
            "data": n.data,
        }
        for n in svc.history
    ]


@router.post("/send")
async def send_notification(request: SendNotificationRequest):
    svc = _get_service()
    notification = Notification(
        notification_type=request.notification_type,
        title=request.title,
        message=request.message,
        data=request.data,
        severity=request.severity,
    )
    await svc.notify(notification)
    return {"status": "sent", "type": request.notification_type.value}


@router.post("/webhooks")
async def configure_webhook(request: WebhookConfigRequest):
    svc = _get_service()
    events = [NotificationType(e) for e in request.events] if request.events else []
    config = WebhookConfig(
        url=request.url,
        secret=request.secret,
        headers=request.headers,
        enabled=request.enabled,
        events=events,
    )
    svc.configure_webhook(config)
    return {"status": "configured", "url": request.url}


@router.post("/email")
async def configure_email(request: EmailConfigRequest):
    svc = _get_service()
    events = [NotificationType(e) for e in request.events] if request.events else []
    config = EmailConfig(
        smtp_host=request.smtp_host,
        smtp_port=request.smtp_port,
        smtp_user=request.smtp_user,
        smtp_password=request.smtp_password,
        from_address=request.from_address,
        to_addresses=request.to_addresses,
        use_tls=request.use_tls,
        enabled=request.enabled,
        events=events,
    )
    svc.configure_email(config)
    return {"status": "configured"}


@router.delete("/history")
async def clear_history():
    svc = _get_service()
    svc.clear_history()
    return {"status": "cleared"}
