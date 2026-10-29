"""Astra trace handlers stay off unless the flag and client are both set."""

from types import SimpleNamespace

from app.ai.tutor.tracing import tutor_callback, with_tutor_trace
from app.core.config import settings
from app.services import product_events


def _thread() -> SimpleNamespace:
    return SimpleNamespace(
        id="thread-1",
        user_id="user-1",
        subject_id="subject-1",
        topic_id=None,
        lesson_id="lesson-1",
        question_id="",
        exam_type="WAEC",
        title="What is photosynthesis?",
        content="Explain the light reaction.",
    )


def test_tracing_off_returns_the_same_config(monkeypatch):
    monkeypatch.setattr(settings, "posthog_ai_tracing", False)
    product_events.use_client(object())
    config = {"configurable": {"thread_id": "thread-1"}}
    assert tutor_callback(_thread(), kind="message") is None
    assert with_tutor_trace(config, _thread(), kind="message") is config
    assert "callbacks" not in config


def test_disabled_client_skips_tracing(monkeypatch):
    monkeypatch.setattr(settings, "posthog_ai_tracing", True)
    product_events.use_client(object())
    product_events.disable()
    assert product_events.client() is None
    config = {"configurable": {"thread_id": "thread-1"}}
    assert tutor_callback(_thread(), kind="stream") is None
    assert with_tutor_trace(config, _thread(), kind="stream") is config


def test_handler_is_private_and_leaves_the_original_config(monkeypatch):
    monkeypatch.setattr(settings, "posthog_ai_tracing", True)
    product_events.use_client(object())
    thread = _thread()
    config = {"configurable": {"thread_id": thread.id, "user_id": thread.user_id}}
    handler = tutor_callback(thread, kind="message")
    assert handler is not None
    assert handler._privacy_mode is True
    assert handler._distinct_id == "user-1"
    assert handler._trace_id is None
    assert handler._properties == {
        "$ai_session_id": "thread-1",
        "agent": "astra",
        "kind": "message",
        "subject_id": "subject-1",
        "lesson_id": "lesson-1",
        "exam_type": "WAEC",
    }
    blob = str(handler._properties)
    assert "photosynthesis" not in blob
    assert "light reaction" not in blob

    traced = with_tutor_trace(config, thread, kind="resume")
    assert traced is not config
    assert "callbacks" not in config
    assert config["configurable"] == {"thread_id": "thread-1", "user_id": "user-1"}
    callbacks = traced["callbacks"]
    assert isinstance(callbacks, list)
    assert len(callbacks) == 1
    assert callbacks[0]._privacy_mode is True
    assert callbacks[0]._properties["kind"] == "resume"
    assert callbacks[0]._properties["$ai_session_id"] == "thread-1"


def test_handler_construction_failure_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "posthog_ai_tracing", True)
    product_events.use_client(object())

    class _Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("posthog down")

    import posthog.ai.langchain as langchain_callbacks

    monkeypatch.setattr(langchain_callbacks, "CallbackHandler", _Boom)
    assert tutor_callback(_thread(), kind="fork") is None
