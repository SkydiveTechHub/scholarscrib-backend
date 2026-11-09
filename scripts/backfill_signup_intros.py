"""Grant the free Standard intro to existing Freemium users.

Run once after Paystack is configured (``PAYSTACK_SECRET_KEY`` set so
``settings.billing_enabled`` is true). New signups already get the intro from
``provision_signup_billing``; this catches accounts created while billing was off.

Usage:
  python -m scripts.backfill_signup_intros --dry-run
  python -m scripts.backfill_signup_intros
  python -m scripts.backfill_signup_intros --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logger import logger  # noqa: E402
from app.database.db import async_session  # noqa: E402
from app.database.models import User  # noqa: E402
from app.services.billing.provisioning import provision_signup_billing  # noqa: E402


async def _candidates(session, *, limit: int | None) -> list[User]:
    """Active freemium users who never received an intro window."""
    stmt = (
        select(User)
        .where(
            User.is_active.is_(True),
            User.tier == "FREEMIUM",
            User.billing_status == "freemium",
            User.intro_ends_at.is_(None),
        )
        .order_by(User.created_at)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def main(*, dry_run: bool, limit: int | None) -> int:
    if not settings.billing_enabled:
        print("billing_enabled=false — set a real PAYSTACK_SECRET_KEY before backfill.")
        return 1

    async with async_session() as session:
        users = await _candidates(session, limit=limit)
        print(f"candidates={len(users)} dry_run={dry_run} limit={limit}")
        if dry_run:
            for user in users[:20]:
                print(f"  would_provision id={user.id} email={user.email}")
            if len(users) > 20:
                print(f"  … and {len(users) - 20} more")
            return 0

        provisioned = 0
        for user in users:
            try:
                await provision_signup_billing(session, user)
                if user.billing_status == "trialing":
                    provisioned += 1
                    await session.commit()
                else:
                    logger.warning(
                        "backfill skipped user=%s status=%s",
                        user.id,
                        user.billing_status,
                    )
                    await session.rollback()
            except Exception:
                logger.exception("backfill failed user=%s", user.id)
                await session.rollback()

        print(f"provisioned={provisioned}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Standard intro for freemium users after billing is enabled"
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates without writing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max users to process (oldest first)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(dry_run=args.dry_run, limit=args.limit)))
