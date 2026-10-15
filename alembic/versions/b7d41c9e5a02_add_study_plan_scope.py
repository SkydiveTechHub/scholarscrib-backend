"""Add StudyPlan.scopeClassLevel and scopeTerm

Revision ID: b7d41c9e5a02
Revises: e8b2c4f7a613
Create Date: 2026-10-15 10:00:00.000000

A plan now stores the class and term it is for. Existing plans are backfilled
with the student's class (SS1 when unset). Before this change plans followed
the live calendar term, so ACTIVE plans get the term live at migration time
(AcademicTerm containing CURRENT_DATE, else the built-in calendar, else FIRST);
INACTIVE plans get the term they were created in (same lookup on createdAt).

Built-in calendar mapping (between terms the upcoming term is used):
Jan 1 - Apr 26 -> SECOND, Apr 27 - Jul 31 -> THIRD, Aug 1 - Dec 31 -> FIRST.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "b7d41c9e5a02"
down_revision = "e8b2c4f7a613"
branch_labels = None
depends_on = None


def upgrade() -> None:
    class_level = postgresql.ENUM(
        "SS1", "SS2", "SS3", name="ClassLevel", create_type=False
    )
    term = postgresql.ENUM("FIRST", "SECOND", "THIRD", name="Term", create_type=False)
    op.add_column("StudyPlan", sa.Column("scopeClassLevel", class_level, nullable=True))
    op.add_column("StudyPlan", sa.Column("scopeTerm", term, nullable=True))
    op.execute(
        """
        UPDATE "StudyPlan" p
        SET "scopeClassLevel" = COALESCE(
            (SELECT u."classLevel" FROM "User" u WHERE u.id = p."studentId"),
            'SS1'
        )
        """
    )
    # active plans followed the live calendar: use today's term
    op.execute(
        """
        UPDATE "StudyPlan" p
        SET "scopeTerm" = COALESCE(
            (
                SELECT t."term" FROM "AcademicTerm" t
                WHERE CURRENT_DATE BETWEEN t."startsOn" AND t."endsOn"
                ORDER BY t."startsOn" DESC
                LIMIT 1
            ),
            CASE
                WHEN EXTRACT(MONTH FROM CURRENT_DATE) IN (1, 2, 3)
                     THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM CURRENT_DATE) = 4
                     AND EXTRACT(DAY FROM CURRENT_DATE) <= 26
                     THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM CURRENT_DATE) IN (4, 5, 6, 7)
                     THEN 'THIRD'::"Term"
                ELSE 'FIRST'::"Term"
            END
        )
        WHERE p."isActive" = true
        """
    )
    # inactive plans keep the term they were created in
    op.execute(
        """
        UPDATE "StudyPlan" p
        SET "scopeTerm" = COALESCE(
            (
                SELECT t."term" FROM "AcademicTerm" t
                WHERE p."createdAt"::date BETWEEN t."startsOn" AND t."endsOn"
                ORDER BY t."startsOn" DESC
                LIMIT 1
            ),
            CASE
                WHEN EXTRACT(MONTH FROM p."createdAt") IN (1, 2, 3)
                     THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM p."createdAt") = 4
                     AND EXTRACT(DAY FROM p."createdAt") <= 26
                     THEN 'SECOND'::"Term"
                WHEN EXTRACT(MONTH FROM p."createdAt") IN (4, 5, 6, 7)
                     THEN 'THIRD'::"Term"
                ELSE 'FIRST'::"Term"
            END
        )
        WHERE p."isActive" IS NOT TRUE
        """
    )
    op.alter_column("StudyPlan", "scopeClassLevel", nullable=False)
    op.alter_column("StudyPlan", "scopeTerm", nullable=False)


def downgrade() -> None:
    op.drop_column("StudyPlan", "scopeTerm")
    op.drop_column("StudyPlan", "scopeClassLevel")
