import asyncio

from app.database.db import get_session
from app.services.admin import CreateAdminService


async def create_superuser():
    async with get_session() as session:
        admin_service = CreateAdminService(session, "superadmin", "admin", "password")

        email, username = admin_service._parse_identifier()
        admin_service._validate_password()
        admin = await admin_service._create_superuser(email, username)

        print("Superadmin created successfully")
        await session.commit()

        return admin


if __name__ == "__main__":
    asyncio.run(create_superuser())
