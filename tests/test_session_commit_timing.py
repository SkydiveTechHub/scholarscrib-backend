"""A write must be committed before its response reaches the client.

The frontend refreshes the moment a save returns, so a commit that lands after
the response is sent makes the next read miss the write.
"""

from fastapi import FastAPI

from app.database import db
from app.database.db import AnSession


async def test_session_commits_before_the_response_is_sent(monkeypatch):
    events: list[str] = []

    class FakeSession:
        async def commit(self):
            events.append("commit")

        async def rollback(self):
            events.append("rollback")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db, "async_session", lambda: FakeSession())

    app = FastAPI()

    @app.post("/write")
    async def write(session: AnSession):
        return {"ok": True}

    async def send_logging_response_end(message):
        if message["type"] == "http.response.body" and not message.get("more_body"):
            events.append("response sent")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/write",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send_logging_response_end)

    assert events == ["commit", "response sent"]
