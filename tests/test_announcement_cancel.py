"""Cancelling an announcement withdraws it from students' in-app inboxes."""

from types import SimpleNamespace

import pytest

from app.core.errors import ApiError
from app.services import console


def _patch(monkeypatch, row):
    calls: dict[str, object] = {"removed": [], "cancelled": []}

    async def by_id(session, announcement_id):
        return row

    async def pending_for(session, announcement_id):
        return [SimpleNamespace(status="PENDING")]

    async def remove_by_source_key(session, source_key):
        calls["removed"].append(source_key)

    class FakeAudit:
        def __init__(self, *args):
            pass

        async def process(self):
            return None

    monkeypatch.setattr(console.announcements_repository, "by_id", by_id)
    monkeypatch.setattr(
        console.announcement_deliveries_repository, "pending_for", pending_for
    )
    monkeypatch.setattr(
        console.in_app_notifications_repository,
        "remove_by_source_key",
        remove_by_source_key,
    )
    monkeypatch.setattr(console, "RecordAuditService", FakeAudit)
    return calls


async def test_cancel_removes_inbox_rows(monkeypatch):
    row = SimpleNamespace(id="a1", title="Mock exam", status="QUEUED")
    calls = _patch(monkeypatch, row)

    result = await console.CancelAnnouncementService(None, "admin", "a1").process()

    assert result == {"ok": True}
    assert row.status == "CANCELLED"
    assert calls["removed"] == ["announcement:a1"]


async def test_cancel_of_a_finished_announcement_leaves_the_inbox_alone(monkeypatch):
    row = SimpleNamespace(id="a1", title="Mock exam", status="SENT")
    calls = _patch(monkeypatch, row)

    with pytest.raises(ApiError):
        await console.CancelAnnouncementService(None, "admin", "a1").process()

    assert calls["removed"] == []
