"""Product events never raise, and they never attach personal data."""

from decimal import Decimal

from app.core.config import settings
from app.services import product_events


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, dict | None]] = []

    def capture(self, event, distinct_id=None, properties=None):
        self.calls.append((event, distinct_id, properties))

    def shutdown(self) -> None:
        return None


class _Boom:
    def capture(self, event, distinct_id=None, properties=None):
        raise RuntimeError("posthog down")

    def shutdown(self) -> None:
        raise RuntimeError("posthog down")


def test_disabled_client_does_not_capture():
    recorder = _Recorder()
    product_events.use_client(recorder)
    product_events.disable()
    product_events.capture(
        "user-1", "user_signed_up", {"signup_method": "email", "email": "a@b.c"}
    )
    product_events.note_user_active("user-1")
    assert recorder.calls == []


def test_failing_client_does_not_raise():
    product_events.use_client(_Boom())
    product_events.capture("user-1", "user_logged_in", {"method": "email"})
    product_events.shutdown_client()


def test_disallowed_keys_are_dropped():
    recorder = _Recorder()
    product_events.use_client(recorder)
    product_events.capture(
        "user-1",
        "user_signed_up",
        {
            "signup_method": "email",
            "email": "ada@example.com",
            "name": "Ada",
            "phone": "0800",
            "content": "hello",
            "percentage": Decimal("80.5"),
            "$set": {"email": "ada@example.com"},
        },
        set_props={
            "class_level": "SS3",
            "track": "SCIENCE",
            "email": "ada@example.com",
            "phone": "0800",
            "first_name": "Ada",
        },
        set_once={"signup_method": "email", "name": "Ada"},
    )
    event, distinct_id, props = recorder.calls[0]
    assert event == "user_signed_up"
    assert distinct_id == "user-1"
    assert props is not None
    assert props["signup_method"] == "email"
    assert props["percentage"] == 80.5
    assert props["$set"] == {"class_level": "SS3", "track": "SCIENCE"}
    assert props["$set_once"] == {"signup_method": "email"}
    blob = str(props)
    assert "ada@example.com" not in blob
    assert "Ada" not in blob
    assert "0800" not in blob
    assert "hello" not in blob


def test_user_active_once_per_lagos_day(monkeypatch):
    recorder = _Recorder()
    product_events.use_client(recorder)
    monkeypatch.setattr(product_events, "_redis_claim", lambda _token: None)
    product_events.note_user_active("user-1")
    product_events.note_user_active("user-1")
    product_events.note_user_active("user-2")
    assert [call[0] for call in recorder.calls] == ["user_active", "user_active"]
    assert [call[1] for call in recorder.calls] == ["user-1", "user-2"]


def test_redis_already_claimed_does_not_capture(monkeypatch):
    recorder = _Recorder()
    product_events.use_client(recorder)
    monkeypatch.setattr(product_events, "_redis_claim", lambda _token: False)
    product_events.note_user_active("user-1")
    product_events.note_user_active("user-1")
    assert recorder.calls == []


def test_missing_token_does_not_raise(monkeypatch):
    product_events.use_client(None)
    monkeypatch.setattr(settings, "posthog_project_token", None)
    product_events.open_client()
    product_events.capture("user-1", "practice_submitted", {"percentage": 80})
    product_events.shutdown_client()
