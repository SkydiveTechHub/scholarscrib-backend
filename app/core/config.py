from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ScholarsCrib API settings.

    Names follow the backend-port environment table. Bearer JWTs replace
    Auth.js cookies: the Next app should send ``Authorization: Bearer``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "ScholarsCrib API"
    database_url: str
    port: int = 8001
    debug: bool = True
    base_dir: Path = Path(__file__).resolve().parent.parent.parent
    reload: bool = True
    db_echo: bool = False
    host: str = "0.0.0.0"
    allowed_origins: list[str] = Field(default_factory=lambda: ["*"])

    auth_secret: str
    admin_auth_secret: str
    auth_google_id: str | None
    auth_google_secret: str | None
    app_url: str

    paystack_secret_key: str | None
    cloudinary_cloud_name: str | None
    cloudinary_api_key: str | None
    cloudinary_api_secret: str | None

    sdash_base_url: str
    sdash_access_token: str | None
    question_provider_enabled: str

    upstash_redis_rest_url: str | None
    upstash_redis_rest_token: str | None
    redis_url: str | None

    vapid_public_key: str | None
    vapid_private_key: str | None
    vapid_subject: str | None
    cron_secret: str | None

    @model_validator(mode="after")
    def secrets_must_differ(self) -> "Settings":
        if self.auth_secret == self.admin_auth_secret:
            raise ValueError(
                "ADMIN_AUTH_SECRET must be set and must differ from AUTH_SECRET"
            )
        return self

    @property
    def billing_enabled(self) -> bool:
        key = (self.paystack_secret_key or "").strip()
        return bool(key) and key != "your-paystack-secret-key"

    @property
    def google_enabled(self) -> bool:
        client_id = (self.auth_google_id or "").strip()
        secret = (self.auth_google_secret or "").strip()
        if not client_id or not secret:
            return False
        return (
            client_id != "your-google-client-id"
            and secret != "your-google-client-secret"
        )

    @property
    def cloudinary_enabled(self) -> bool:
        return bool(
            self.cloudinary_cloud_name
            and self.cloudinary_api_key
            and self.cloudinary_api_secret
        )

    @property
    def provider_enabled(self) -> bool:
        return self.question_provider_enabled == "true"

    @property
    def push_enabled(self) -> bool:
        subject = self.vapid_subject or ""
        if not (
            self.vapid_public_key
            and self.vapid_private_key
            and subject
            and self.cron_secret
            and len(self.cron_secret) >= 16
        ):
            return False
        return subject.startswith("mailto:") or subject.startswith("https:")


settings = Settings()  # type: ignore
