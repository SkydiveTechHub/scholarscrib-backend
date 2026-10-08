"""Raw inputs for the admin overview's analytics panel.

The rules (windows, month keys, subscriber counts, renewals) live in the
frontend's `admin-analytics`; this only counts rows. Every window boundary
arrives as a parameter so there is a single definition of "this month so far".
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import as_utc

# Casting parameters and bucketed columns to `timestamptz` keeps these queries
# correct whether a column is `timestamp` (UTC) or `timestamptz`.
_NOW = "CAST(:now AS timestamptz)"
_SINCE = "CAST(:since AS timestamptz)"
_CUR_START = "CAST(:cur_start AS timestamptz)"
_PREV_START = "CAST(:prev_start AS timestamptz)"
_PREV_END = "CAST(:prev_end AS timestamptz)"
_LAST_MONTH = "CAST(:last_month AS timestamptz)"
_COHORT_START = "CAST(:cohort_start AS timestamptz)"


def _bucket(column: str) -> str:
    return f"to_char(({column})::timestamptz AT TIME ZONE 'Africa/Lagos', 'YYYY-MM')"


# Same cover rule as the entitlement check: active, started, not yet ended.
_LIVE_SUBSCRIPTION = f"""EXISTS (
    SELECT 1 FROM "Subscription" s
    WHERE s."userId" = u."id" AND s."status" = 'ACTIVE'
      AND s."endsAt" > {_NOW}
      AND (s."startsAt" IS NULL OR s."startsAt" <= {_NOW}))"""


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value else None


class GetAdminAnalyticsService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        since: datetime,
        current_start: datetime,
        previous_start: datetime,
        previous_end: datetime,
        last_month: datetime,
        cohort_start: datetime,
    ) -> None:
        self.session = session
        self.params = {
            "now": as_utc(now),
            "since": as_utc(since),
            "cur_start": as_utc(current_start),
            "prev_start": as_utc(previous_start),
            "prev_end": as_utc(previous_end),
            "last_month": as_utc(last_month),
            "cohort_start": as_utc(cohort_start),
        }

    async def _rows(self, sql: str) -> list[dict]:
        # Bind only the parameters a statement uses.
        used = {k: v for k, v in self.params.items() if f":{k}" in sql}
        result = await self.session.execute(text(sql), used)
        return [dict(row) for row in result.mappings().all()]

    async def process(self) -> dict:
        users = (
            await self._rows(
                f"""SELECT count(*)::int AS total,
                  count(*) FILTER (WHERE "createdAt" < {_SINCE})::int AS before,
                  count(*) FILTER (WHERE "createdAt" < {_LAST_MONTH})::int AS "lastMonth",
                  count(*) FILTER (WHERE "createdAt" >= {_CUR_START}
                    AND "createdAt" < {_NOW})::int AS cur,
                  count(*) FILTER (WHERE "createdAt" >= {_PREV_START}
                    AND "createdAt" < {_PREV_END})::int AS prev
                FROM "User" WHERE "role" = 'STUDENT'"""
            )
        )[0]
        signups_by_month = await self._rows(
            f"""SELECT {_bucket('"createdAt"')} AS month, count(*)::int AS n
                FROM "User" WHERE "role" = 'STUDENT' AND "createdAt" >= {_SINCE}
                GROUP BY 1"""
        )
        learners = (
            await self._rows(
                f"""SELECT
                  count(DISTINCT "studentId") FILTER (WHERE "occurredAt" >= {_CUR_START})::int AS cur,
                  count(DISTINCT "studentId") FILTER (WHERE "occurredAt" < {_PREV_END})::int AS prev
                FROM "LearningEvent"
                WHERE "occurredAt" >= {_PREV_START} AND "occurredAt" < {_NOW}"""
            )
        )[0]
        learners_by_month = await self._rows(
            f"""SELECT {_bucket('"occurredAt"')} AS month,
                  count(DISTINCT "studentId")::int AS n
                FROM "LearningEvent" WHERE "occurredAt" >= {_SINCE} GROUP BY 1"""
        )
        assessments = (
            await self._rows(
                f"""SELECT
                  count(*) FILTER (WHERE "completedAt" >= {_CUR_START})::int AS cur,
                  count(*) FILTER (WHERE "completedAt" < {_PREV_END})::int AS prev
                FROM "AssessmentAttempt"
                WHERE "status" = 'COMPLETED' AND "completedAt" >= {_PREV_START}
                  AND "completedAt" < {_NOW}"""
            )
        )[0]
        assessments_by_month = await self._rows(
            f"""SELECT {_bucket('"completedAt"')} AS month, count(*)::int AS n
                FROM "AssessmentAttempt"
                WHERE "status" = 'COMPLETED' AND "completedAt" >= {_SINCE} GROUP BY 1"""
        )
        # Active rows of students only: staff test purchases are real rows but
        # not customers. Covers the renewal look-back and every running term.
        subscriptions = await self._rows(
            f"""SELECT s."userId", s."tier"::text AS tier, s."status"::text AS status,
                  s."source"::text AS source, s."amountKobo", s."paidAt",
                  s."startsAt", s."endsAt"
                FROM "Subscription" s JOIN "User" u ON u."id" = s."userId"
                WHERE s."status" = 'ACTIVE' AND u."role" = 'STUDENT'
                  AND (s."endsAt" >= {_SINCE} OR s."paidAt" >= {_SINCE})"""
        )
        for row in subscriptions:
            for key in ("paidAt", "startsAt", "endsAt"):
                row[key] = _iso(row[key])
        class_levels = await self._rows(
            f"""SELECT u."classLevel"::text AS label, count(*)::int AS students,
                  count(*) FILTER (WHERE {_LIVE_SUBSCRIPTION})::int AS subscribers
                FROM "User" u WHERE u."role" = 'STUDENT' GROUP BY 1"""
        )
        states = await self._rows(
            f"""SELECT NULLIF(u."state", '') AS label, count(*)::int AS students,
                  count(*) FILTER (WHERE {_LIVE_SUBSCRIPTION})::int AS subscribers
                FROM "User" u WHERE u."role" = 'STUDENT' GROUP BY 1"""
        )
        # Each step is counted against the whole cohort, not the step before.
        funnel = (
            await self._rows(
                f"""SELECT count(*)::int AS "signedUp",
                  count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM "LearningEvent" e WHERE e."studentId" = u."id"))::int AS practised,
                  count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM "AssessmentAttempt" a
                    WHERE a."studentId" = u."id" AND a."status" = 'COMPLETED'))::int AS assessed,
                  count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM "Subscription" s
                    WHERE s."userId" = u."id" AND s."source" = 'PAYSTACK'
                      AND s."status" = 'ACTIVE'))::int AS paid
                FROM "User" u
                WHERE u."role" = 'STUDENT' AND u."createdAt" >= {_COHORT_START}"""
            )
        )[0]

        return {
            "users": users,
            "signupsByMonth": signups_by_month,
            "learners": learners,
            "learnersByMonth": learners_by_month,
            "assessments": assessments,
            "assessmentsByMonth": assessments_by_month,
            "subscriptions": subscriptions,
            "classLevels": class_levels,
            "states": states,
            "funnel": funnel,
        }
