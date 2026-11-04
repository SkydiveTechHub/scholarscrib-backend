"""Generic transactional email service with Jinja2 template rendering."""

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.logger import logger
from app.core.timeutil import utcnow

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "emails"

_jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=True,
)

_BLOCK_TAGS = {"p", "div", "tr", "h1", "h2", "h3", "li", "table"}
# Void elements have no end tag, so they must not touch the skip depth.
_VOID_TAGS = {"meta", "br", "img", "link", "hr", "input"}


class _TextExtractor(HTMLParser):
    """Turns a rendered email into readable plain text (links keep their URL)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in _VOID_TAGS:
            if tag == "br" and not self._skip:
                self.parts.append("\n")
            return
        if (
            self._skip
            or tag in {"style", "head", "title"}
            or attributes.get("data-text") == "skip"
        ):
            self._skip += 1
            return
        if tag == "a":
            self._href = attributes.get("href")
            self._link_text = []
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<br />` style: void tags only open, anything else open-and-closes.
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._skip:
            self._skip -= 1
            return
        if tag == "a" and self._href:
            text = "".join(self._link_text).strip()
            # A link whose text is the URL itself needs no repeat.
            if text and self._href not in text and not self._href.startswith("mailto:"):
                self.parts.append(f" ({self._href})")
            self._href = None
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


def html_to_text(html: str) -> str:
    """Plain-text alternative for the HTML part: no tags, tidy blank lines."""
    parser = _TextExtractor()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


class EmailService:
    """Generic service to render Jinja2 templates and trigger email delivery."""

    @staticmethod
    def _brand() -> dict[str, str]:
        base = settings.email_asset_url.rstrip("/")
        return {
            "name": "ScholarsCrib",
            "site_url": base,
            "logo_url": f"{base}/logo-on-light.png",
            "icon_url": f"{base}/icon-192.png",
        }

    @classmethod
    async def render_template(cls, template_name: str, context: dict[str, Any]) -> str:
        template = _jinja_env.get_template(template_name)
        shared = {"brand": cls._brand(), "year": utcnow().year}
        return await template.render_async(**{**shared, **context})

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
        text = text or html_to_text(html)

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
                            "text": text,
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
