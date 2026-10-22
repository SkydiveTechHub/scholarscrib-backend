"""Unit tests for tutor stream cancel tokens."""

from app.services.rag.stream_control import (
    begin_stream,
    end_stream,
    request_stop,
    reset_for_tests,
    should_stop,
)


def setup_function():
    reset_for_tests()


def test_stop_marks_active_stream():
    begin_stream("thread-1", "stream-a")
    assert should_stop("thread-1", "stream-a") is False
    out = request_stop("thread-1", "stream-a")
    assert out["cancelled"] is True
    assert out["streamId"] == "stream-a"
    assert should_stop("thread-1", "stream-a") is True
    end_stream("thread-1", "stream-a")


def test_stop_without_stream_id_cancels_any():
    begin_stream("thread-2", "stream-b")
    request_stop("thread-2")
    assert should_stop("thread-2", "stream-b") is True
    end_stream("thread-2", "stream-b")
