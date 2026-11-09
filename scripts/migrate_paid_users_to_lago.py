"""Preserve live Paystack/COMP term holders under Lago billing fields.

Usage:
  python -m scripts.migrate_paid_users_to_lago --dry-run
  python -m scripts.migrate_paid_users_to_lago
  python -m scripts.migrate_paid_users_to_lago --no-lago
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database.db import async_session  # noqa: E402
from app.services.billing.migrate import (  # noqa: E402
    ensure_freemium_plan_fields,
    migrate_live_paid_users,
    migration_summary,
)


async def main(*, dry_run: bool, push_lago: bool) -> None:
    async with async_session() as session:
        result = await migrate_live_paid_users(
            session, push_lago=push_lago, dry_run=dry_run
        )
        freemium = await ensure_freemium_plan_fields(session, dry_run=dry_run)
        if not dry_run:
            await session.commit()
        print(migration_summary(result))
        print(f"freemium_backfill={freemium} dry_run={dry_run}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates without writing",
    )
    parser.add_argument(
        "--no-lago",
        action="store_true",
        help="Only update local User/Subscription rows",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, push_lago=not args.no_lago))
