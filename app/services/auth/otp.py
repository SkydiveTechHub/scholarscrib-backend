"""OTP generation and verification service for students."""

import secrets
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import hit
from app.core.timeutil import utcnow
from app.database.repositories.identity import (
    users_repository,
    verification_tokens_repository,
)
from app.services.email import EmailService

OTP_LIFETIME = timedelta(minutes=10)


class StudentOTPService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _identifier(email: str) -> str:
        return f"student:otp:{email.strip().lower()}"

    async def send_otp(self, email: str) -> None:
        normalised = email.strip().lower()
        # Rate limit: 5 OTP sends per 10 minutes per email
        hit(f"otp-send:{normalised}", 5, 600)

        # Generate 6-digit code
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = utcnow() + OTP_LIFETIME

        await verification_tokens_repository.create_token(
            self.session,
            identifier=self._identifier(normalised),
            token=code,
            expires=expires_at,
        )

        await EmailService.send_email(
            to_email=normalised,
            subject=f"{code} is your ScholarsCrib verification code",
            template_name="student_otp.html",
            context={"otp_code": code, "email": normalised},
        )

    async def verify_otp(self, email: str, code: str) -> tuple[bool, str]:
        normalised = email.strip().lower()
        cleaned_code = code.strip()

        token_row = await verification_tokens_repository.get_valid_token(
            self.session,
            identifier=self._identifier(normalised),
            token=cleaned_code,
        )
        if token_row is None:
            return False, "Invalid or expired verification code"

        # Remove used token
        await verification_tokens_repository.delete_by_identifier(
            self.session, self._identifier(normalised)
        )

        # Mark user's email as verified if they already registered
        user = await users_repository.by_email(self.session, normalised)
        if user is not None and user.email_verified is None:
            user.email_verified = utcnow()
            await users_repository.update(
                self.session, user, update_data={"email_verified": user.email_verified}
            )

        return True, "Email verified successfully"
