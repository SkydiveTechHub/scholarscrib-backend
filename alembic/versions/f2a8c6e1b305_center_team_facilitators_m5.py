"""M5: CenterInvite.kind for facilitator invites (Team plan).

Revision ID: f2a8c6e1b305
Revises: e5b1c7d9a204
Create Date: 2026-11-12 16:00:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f2a8c6e1b305"
down_revision = "e5b1c7d9a204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    invite_kind = postgresql.ENUM(
        "STUDENT",
        "FACILITATOR",
        name="CenterInviteKind",
        create_type=False,
    )
    invite_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "CenterInvite",
        sa.Column(
            "kind",
            postgresql.ENUM(
                "STUDENT",
                "FACILITATOR",
                name="CenterInviteKind",
                create_type=False,
            ),
            nullable=False,
            server_default="STUDENT",
        ),
    )
    op.create_index(
        "CenterInvite_centerId_kind_idx",
        "CenterInvite",
        ["centerId", "kind"],
    )


def downgrade() -> None:
    op.drop_index("CenterInvite_centerId_kind_idx", table_name="CenterInvite")
    op.drop_column("CenterInvite", "kind")
    op.execute('DROP TYPE IF EXISTS "CenterInviteKind"')
