"""Welcome email sent once a student's account is ready."""

from app.core.config import settings
from app.core.logger import logger
from app.database.models import User
from app.services.email import EmailService


async def send_welcome_email(user: User) -> None:
    """Send the welcome email. Never raises: delivery must not break signup."""
    if not user.email:
        return
    try:
        await EmailService.send_email(
            to_email=user.email,
            subject="Welcome to ScholarsCrib",
            template_name="student_welcome.html",
            context={
                "first_name": user.first_name,
                "email": user.email,
                "dashboard_url": f"{settings.app_url.rstrip('/')}/dashboard",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to send welcome email to user %s", user.id)
