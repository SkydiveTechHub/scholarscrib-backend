import pytest

from app.services.assessments import jamb
from app.services.provider.base import SearchPage
from app.services.provider.service import _DISCOVERY_CACHE


class FakeProvider:
    """Serves canned pages: `pages[cursor]` -> (numbers, next cursor)."""

    name = "ALOC"
    configured = True

    def __init__(self, pages=None, years=None, fail_at=None):
        self.pages = pages or {}
        self.years = years
        self.fail_at = fail_at
        self.calls = []

    async def search(self, *, subject_slug, exam_type, exam_year, limit, cursor=None):
        self.calls.append((subject_slug, exam_type, exam_year, limit, cursor))
        if self.fail_at is not None and cursor == self.fail_at:
            return SearchPage(status_code=503)
        numbers, next_cursor = self.pages[cursor]
        return SearchPage(
            items=[{"questionNumber": n} for n in numbers],
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
        )

    async def discover(self, resource, key=None):
        return self.years


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    _DISCOVERY_CACHE.clear()
    jamb._YEAR_COUNTS.clear()
    yield
    _DISCOVERY_CACHE.clear()
    jamb._YEAR_COUNTS.clear()


async def test_walks_every_page_of_a_paper_in_order():
    provider = FakeProvider(
        pages={None: ([1, 2], "c2"), "c2": ([3, 4], "c3"), "c3": ([5], None)}
    )
    items, complete = await jamb._walk_paper(provider, "physics", 2011)
    assert complete
    assert [item["questionNumber"] for item in items] == [1, 2, 3, 4, 5]
    # Always the provider's page cap, for the JAMB board and the asked year.
    assert {call[1:4] for call in provider.calls} == {("JAMB", 2011, 15)}


async def test_a_failed_page_keeps_what_was_walked_but_is_not_complete():
    provider = FakeProvider(pages={None: ([1, 2], "c2")}, fail_at="c2")
    items, complete = await jamb._walk_paper(provider, "physics", 2011)
    assert not complete
    assert len(items) == 2


async def test_a_paper_the_provider_does_not_have_is_complete_and_empty():
    class Missing(FakeProvider):
        async def search(self, **kwargs):
            return SearchPage(status_code=404)

    items, complete = await jamb._walk_paper(Missing(), "physics", 1990)
    assert complete
    assert items == []


async def test_year_counts_read_the_jamb_breakdown_only():
    provider = FakeProvider(
        years=[
            {"year": 2010, "questionCount": 75, "breakdown": {"jamb": 75}},
            {"year": 2011, "questionCount": 30, "breakdown": {"waec": 30}},
            {"year": "bad", "breakdown": {"jamb": 9}},
            {"year": 2012, "breakdown": {"jamb": 0}},
        ]
    )
    assert await jamb._jamb_year_counts(provider, "english-language") == {2010: 75}
