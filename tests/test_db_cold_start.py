import pytest
from sqlalchemy.util import greenlet_spawn

from app.database import db


class FakeDialect:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def connect(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionRefusedError("database is waking")
        return "connection"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(db, "_WAKE_BACKOFF", (0, 0))


async def test_connect_retries_until_database_wakes():
    dialect = FakeDialect(failures=2)

    conn = await greenlet_spawn(db._connect_with_retry, dialect, None, (), {})

    assert conn == "connection"
    assert dialect.calls == 3


async def test_connect_gives_up_after_last_attempt():
    dialect = FakeDialect(failures=99)

    with pytest.raises(ConnectionRefusedError):
        await greenlet_spawn(db._connect_with_retry, dialect, None, (), {})

    assert dialect.calls == 3
