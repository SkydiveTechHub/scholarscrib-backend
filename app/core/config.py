from pathlib import Path
from typing import Literal

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

    sdash_base_url: str = "https://www.sdashapi.com/api"
    sdash_access_token: str | None
    aloc_base_url: str = "https://dev.aloc.com.ng/api/v1"
    aloc_api_key: str | None = None
    aloc_fetch_explanations: bool = False
    question_provider: Literal["SDASH", "ALOC"] = "ALOC"
    question_provider_enabled: str

    upstash_redis_rest_url: str | None
    upstash_redis_rest_token: str | None
    redis_url: str | None

    vapid_public_key: str | None = None
    vapid_private_key: str | None
    vapid_subject: str | None
    cron_secret: str | None

    resend_api_key: str | None = None
    email_from: str = "noreply@scholarscrib.com"
    frontend_url: str = "https://scholarscrib.vercel.app"

    # AI tutor / RAG (provider-agnostic; switch via provider:model strings)
    ai_tutor_enabled: bool = False
    llm_model: str = "openai:gpt-4.1-mini"
    llm_grader_model: str | None = None
    embedding_model: str = "openai:text-embedding-3-small"
    reranker_model: str | None = None
    zilliz_uri: str | None = None
    zilliz_token: str | None = None
    milvus_collection: str = "scholarscrib_rag"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None
    cohere_api_key: str | None = None
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "scholarscrib-tutor"
    ai_tutor_daily_message_limit: int = 50
    rag_retrieve_k: int = 8
    rag_rerank_k: int = 4
    rag_max_rewrite_loops: int = 2
    rag_summarize_after_messages: int = 12
    rag_keep_recent_messages: int = 6
    # TypeSafe Jev decision model (routing / yes-no / grading)
    typesafe_api_key: str | None = None
    typesafe_base_url: str | None = None
    jev_model: str = "jev-latest"
    jev_enabled: bool = True
    jev_confidence_threshold: float = 0.7
    jev_grade_noul_threshold: float = 0.55
    jev_guardrails_enabled: bool = True
    jev_guard_policy: str = "strict"
    jev_guard_review_threshold: float = 0.35
    jev_guard_action_threshold: float = 0.7
    jev_guard_severity_block: float = 2.0

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

    @property
    def rag_enabled(self) -> bool:
        return bool(
            self.ai_tutor_enabled
            and self.zilliz_uri
            and self.zilliz_token
            and self.milvus_collection
        )

    @property
    def effective_grader_model(self) -> str:
        return (self.llm_grader_model or self.llm_model).strip()

    @property
    def jev_ready(self) -> bool:
        """True when Jev decisions should be attempted (kill switch + API key)."""
        return bool(self.jev_enabled and (self.typesafe_api_key or "").strip())

    @property
    def jev_guardrails_ready(self) -> bool:
        """True when input/output/tool guardrails should run."""
        return bool(self.jev_ready and self.jev_guardrails_enabled)


settings = Settings()  # type: ignore
