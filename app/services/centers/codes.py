"""Invite short codes and link tokens."""

from __future__ import annotations

import secrets
import string

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repositories.centers import invites_repository

_CODE_ALPHABET = string.ascii_uppercase + string.digits
# Avoid ambiguous characters in WhatsApp-typed codes.
_CODE_ALPHABET = "".join(ch for ch in _CODE_ALPHABET if ch not in "01OI")


def invite_token() -> str:
    return secrets.token_urlsafe(24)


async def unique_invite_code(session: AsyncSession, *, length: int = 8) -> str:
    for _ in range(24):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
        if not await invites_repository.code_taken(session, code):
            return code
    raise RuntimeError("Could not allocate a unique invite code")
