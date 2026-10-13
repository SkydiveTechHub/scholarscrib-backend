"""Generic transactional email service with Jinja2 template rendering."""

from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.logger import logger

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "emails"

_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=True,
)


class EmailService:
    """Generic service to render Jinja2 templates and trigger email delivery."""

    @classmethod
    async def render_template(cls, template_name: str, context: dict[str, Any]) -> str:
        template = _jinja_env.get_template(template_name)
        return await template.render_async(**context)

    @classmethod
    async def send_email(
        cls,
        to_email: str,
        subject: str,
        template_name: str,
        context: dict[str, Any],
        text: str | None = None,
    ) -> bool:
        html = await cls.render_template(template_name, context)

        if settings.resend_api_key:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        "https://api.resend.com/emails",
                        headers={
                            "Authorization": f"Bearer {settings.resend_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "from": settings.email_from,
                            "to": [to_email],
                            "subject": subject,
                            "html": html,
                            "text": text or html,
                        },
                    )
                    if response.status_code < 400:
                        return True
                    logger.error(
                        "Resend API error (%s): %s",
                        response.status_code,
                        response.text,
                    )
            except Exception as exc:
                logger.error("Failed to send email via Resend: %s", exc)

        # Fallback to local console log (useful for dev and test environments)
        logger.info(
            "\n==================== [OUTGOING EMAIL] ====================\n"
            "To: %s\n"
            "Subject: %s\n"
            "Template: %s\n"
            "Context: %s\n"
            "==========================================================\n",
            to_email,
            subject,
            template_name,
            context,
        )
        return True
