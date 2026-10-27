from datetime import UTC, datetime

from app.api.admin import calibration
from app.api.deps import require_admin


def route():
    return next(
        found for found in calibration.router.routes if found.path == "/calibration"
    )


def test_route_requires_an_admin():
    calls = [dependency.call for dependency in route().dependant.dependencies]
    assert require_admin in calls


def test_route_accepts_the_documented_filters():
    names = {param.name for param in route().dependant.query_params}
    assert names == {"scoringVersion", "subjectId", "since", "source"}


async def test_handler_passes_filters_to_the_service(monkeypatch):
    seen = {}

    class FakeService:
        def __init__(self, session, **filters):
            seen.update(filters)

        async def process(self):
            return {"overall": {}, "buckets": [], "byEvidence": {}, "byDifficulty": {}}

    monkeypatch.setattr(calibration, "GetCalibrationReportService", FakeService)
    since = datetime(2026, 10, 1, tzinfo=UTC)
    await calibration.admin_calibration(
        session=object(),
        admin=object(),
        scoringVersion=3,
        subjectId="s1",
        since=since,
        source="LESSON_CHECK",
    )
    assert seen == {
        "scoring_version": 3,
        "subject_id": "s1",
        "since": since,
        "source": "LESSON_CHECK",
    }
