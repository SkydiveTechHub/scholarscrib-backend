"""Seed local billing catalog and optionally mirror plans to the active provider.

Usage:
  python -m scripts.seed_billing_catalog
  python -m scripts.seed_billing_catalog --push-remote
  python -m scripts.seed_billing_catalog --push-lago
  # alias; Lago only if BILLING_PROVIDER=lago
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.database.db import async_session  # noqa: E402
from app.services.billing.catalog import seed_catalog  # noqa: E402
from app.services.billing.providers import get_billing_provider  # noqa: E402


async def main(push_remote: bool) -> None:
    async with async_session() as session:
        counts = await seed_catalog(session)
        await session.commit()
        print(f"Seeded catalog: {counts}")
        if not push_remote:
            return
        provider = get_billing_provider()
        print(f"Pushing catalog via billing_provider={provider.name}")
        from app.api.admin.billing import _push_catalog_to_provider

        if provider.name == "lago" and not settings.lago_enabled:
            print("LAGO_API_KEY not set; skipping Lago push")
            return
        if provider.name == "paystack" and not settings.billing_enabled:
            print("PAYSTACK_SECRET_KEY not set; skipping Paystack push")
            return
        await _push_catalog_to_provider(session)
        await session.commit()
        print(f"Pushed catalog to {provider.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--push-remote",
        "--push-lago",
        dest="push_remote",
        action="store_true",
        help="Create/update plans on the active billing provider (Paystack or Lago)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.push_remote))
