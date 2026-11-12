"""Tutor Centers foundations: Role.TUTOR, center tables, attempt.assignment_id.

Revision ID: a1c4e8f2b903
Revises: b7c2e9a1d004
Create Date: 2026-11-12 14:30:00.000000

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1c4e8f2b903"
down_revision = "b7c2e9a1d004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE \"Role\" ADD VALUE IF NOT EXISTS 'TUTOR'")

    center_membership_kind = postgresql.ENUM(
        "OWNER",
        "FACILITATOR",
        "STUDENT",
        name="CenterMembershipKind",
        create_type=False,
    )
    center_membership_status = postgresql.ENUM(
        "INVITED",
        "ACTIVE",
        "LEFT",
        "REMOVED",
        name="CenterMembershipStatus",
        create_type=False,
    )
    center_plan = postgresql.ENUM(
        "FREE", "SOLO", "TEAM", name="CenterPlan", create_type=False
    )
    assignment_lifecycle = postgresql.ENUM(
        "DRAFT",
        "SCHEDULED",
        "OPEN",
        "CLOSED",
        name="AssignmentLifecycle",
        create_type=False,
    )
    assignment_task_type = postgresql.ENUM(
        "TOPIC_QUIZ",
        "PAST_PAPER",
        "MOCK_EXAM",
        "LESSON",
        "FLASHCARDS",
        name="AssignmentTaskType",
        create_type=False,
    )
    assignment_target_kind = postgresql.ENUM(
        "STUDENT", "GROUP", "CENTER", name="AssignmentTargetKind", create_type=False
    )
    recommendation_status = postgresql.ENUM(
        "DELIVERED", "SEEN", "ACTED", name="RecommendationStatus", create_type=False
    )
    for enum_type in (
        center_membership_kind,
        center_membership_status,
        center_plan,
        assignment_lifecycle,
        assignment_task_type,
        assignment_target_kind,
        recommendation_status,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "Center",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ownerId", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("lga", sa.String(), nullable=True),
        sa.Column("subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("levels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "plan",
            postgresql.ENUM(
                "FREE", "SOLO", "TEAM", name="CenterPlan", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("archivedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ownerId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("Center_ownerId_idx", "Center", ["ownerId"])

    op.create_table(
        "CenterMembership",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("userId", sa.String(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "OWNER",
                "FACILITATOR",
                "STUDENT",
                name="CenterMembershipKind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "INVITED",
                "ACTIVE",
                "LEFT",
                "REMOVED",
                name="CenterMembershipStatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("consent", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("joinedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leftAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["userId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("centerId", "userId", name="CenterMembership_center_user"),
    )
    op.create_index("CenterMembership_userId_idx", "CenterMembership", ["userId"])
    op.create_index(
        "CenterMembership_center_status_idx",
        "CenterMembership",
        ["centerId", "status"],
    )

    op.create_table(
        "CenterGroup",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("centerId", "name", name="CenterGroup_center_name"),
    )

    op.create_table(
        "CenterGroupMember",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("groupId", sa.String(), nullable=False),
        sa.Column("membershipId", sa.String(), nullable=False),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["groupId"], ["CenterGroup.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["membershipId"], ["CenterMembership.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "groupId",
            "membershipId",
            name="CenterGroupMember_group_membership",
        ),
    )

    op.create_table(
        "CenterInvite",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("createdById", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("boundEmail", sa.String(), nullable=True),
        sa.Column("boundPhone", sa.String(), nullable=True),
        sa.Column("useLimit", sa.Integer(), nullable=True),
        sa.Column("useCount", sa.Integer(), nullable=False),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["createdById"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="CenterInvite_code"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("CenterInvite_centerId_idx", "CenterInvite", ["centerId"])

    op.create_table(
        "Assignment",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("createdById", sa.String(), nullable=False),
        sa.Column("assessmentId", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column(
            "taskType",
            postgresql.ENUM(
                "TOPIC_QUIZ",
                "PAST_PAPER",
                "MOCK_EXAM",
                "LESSON",
                "FLASHCARDS",
                name="AssignmentTaskType",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "SCHEDULED",
                "OPEN",
                "CLOSED",
                name="AssignmentLifecycle",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("opensAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dueAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assessmentId"], ["Assessment.id"]),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["createdById"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "Assignment_centerId_status_idx", "Assignment", ["centerId", "status"]
    )
    op.create_index(
        "Assignment_centerId_createdAt_idx", "Assignment", ["centerId", "createdAt"]
    )

    op.create_table(
        "AssignmentTarget",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("assignmentId", sa.String(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "STUDENT",
                "GROUP",
                "CENTER",
                name="AssignmentTargetKind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("targetId", sa.String(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assignmentId"], ["Assignment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assignmentId",
            "kind",
            "targetId",
            name="AssignmentTarget_assignment_kind_target",
        ),
    )
    op.create_index(
        "AssignmentTarget_assignmentId_idx", "AssignmentTarget", ["assignmentId"]
    )

    op.create_table(
        "Recommendation",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("assignmentId", sa.String(), nullable=True),
        sa.Column("authorId", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DELIVERED",
                "SEEN",
                "ACTED",
                name="RecommendationStatus",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["assignmentId"], ["Assignment.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["authorId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("Recommendation_studentId_idx", "Recommendation", ["studentId"])
    op.create_index("Recommendation_centerId_idx", "Recommendation", ["centerId"])

    op.create_table(
        "TutorAccessAudit",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actorId", sa.String(), nullable=False),
        sa.Column("centerId", sa.String(), nullable=False),
        sa.Column("studentId", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actorId"], ["User.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["centerId"], ["Center.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["studentId"], ["User.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "TutorAccessAudit_centerId_createdAt_idx",
        "TutorAccessAudit",
        ["centerId", "createdAt"],
    )
    op.create_index("TutorAccessAudit_studentId_idx", "TutorAccessAudit", ["studentId"])
    op.create_index("TutorAccessAudit_actorId_idx", "TutorAccessAudit", ["actorId"])

    op.add_column(
        "AssessmentAttempt", sa.Column("assignmentId", sa.String(), nullable=True)
    )
    op.create_foreign_key(
        "AssessmentAttempt_assignmentId_fkey",
        "AssessmentAttempt",
        "Assignment",
        ["assignmentId"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "AssessmentAttempt_assignmentId_idx", "AssessmentAttempt", ["assignmentId"]
    )


def downgrade() -> None:
    op.drop_index("AssessmentAttempt_assignmentId_idx", table_name="AssessmentAttempt")
    op.drop_constraint(
        "AssessmentAttempt_assignmentId_fkey",
        "AssessmentAttempt",
        type_="foreignkey",
    )
    op.drop_column("AssessmentAttempt", "assignmentId")

    op.drop_index("TutorAccessAudit_actorId_idx", table_name="TutorAccessAudit")
    op.drop_index("TutorAccessAudit_studentId_idx", table_name="TutorAccessAudit")
    op.drop_index(
        "TutorAccessAudit_centerId_createdAt_idx", table_name="TutorAccessAudit"
    )
    op.drop_table("TutorAccessAudit")

    op.drop_index("Recommendation_centerId_idx", table_name="Recommendation")
    op.drop_index("Recommendation_studentId_idx", table_name="Recommendation")
    op.drop_table("Recommendation")

    op.drop_index("AssignmentTarget_assignmentId_idx", table_name="AssignmentTarget")
    op.drop_table("AssignmentTarget")

    op.drop_index("Assignment_centerId_createdAt_idx", table_name="Assignment")
    op.drop_index("Assignment_centerId_status_idx", table_name="Assignment")
    op.drop_table("Assignment")

    op.drop_index("CenterInvite_centerId_idx", table_name="CenterInvite")
    op.drop_table("CenterInvite")

    op.drop_table("CenterGroupMember")
    op.drop_table("CenterGroup")

    op.drop_index("CenterMembership_center_status_idx", table_name="CenterMembership")
    op.drop_index("CenterMembership_userId_idx", table_name="CenterMembership")
    op.drop_table("CenterMembership")

    op.drop_index("Center_ownerId_idx", table_name="Center")
    op.drop_table("Center")

    for name in (
        "RecommendationStatus",
        "AssignmentTargetKind",
        "AssignmentTaskType",
        "AssignmentLifecycle",
        "CenterPlan",
        "CenterMembershipStatus",
        "CenterMembershipKind",
    ):
        op.execute(f'DROP TYPE IF EXISTS "{name}"')
    # Role.TUTOR enum value cannot be removed safely; leave it in place.
