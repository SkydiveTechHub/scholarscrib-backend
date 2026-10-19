from types import SimpleNamespace

from app.services.learning.library_shelf import library_shelf


def _subject(**kwargs):
    base = {
        "id": "s1",
        "name": "Mathematics",
        "slug": "mathematics",
        "code": "MTH",
        "track_category": "CORE",
        "is_active": True,
    }
    return SimpleNamespace(**{**base, **kwargs})


def test_shelf_lists_subjects_with_resource_counts():
    shelf = library_shelf(
        [
            _subject(),
            _subject(id="s2", name="Physics", code="PHY", track_category="SCIENCE"),
        ],
        {"s1": 3},
        "SCIENCE",
    )
    assert [row["name"] for row in shelf] == ["Mathematics", "Physics"]
    assert shelf[0]["trackCategory"] == "CORE"
    assert shelf[0]["resourceCount"] == 3
    # A subject with nothing uploaded still appears on the shelf.
    assert shelf[1]["resourceCount"] == 0


def test_shelf_is_limited_to_core_and_the_students_track():
    subjects = [
        _subject(id="s1", name="English", track_category="CORE"),
        _subject(id="s2", name="Physics", track_category="SCIENCE"),
        _subject(id="s3", name="Government", track_category="ARTS"),
    ]
    names = [row["name"] for row in library_shelf(subjects, {}, "SCIENCE")]
    assert names == ["English", "Physics"]


def test_shelf_without_a_track_lists_everything():
    subjects = [
        _subject(id="s1", track_category="CORE"),
        _subject(id="s2", name="Government", track_category="ARTS"),
    ]
    assert len(library_shelf(subjects, {}, None)) == 2


def test_shelf_hides_inactive_subjects():
    subjects = [_subject(), _subject(id="s2", name="Old", is_active=False)]
    assert [row["name"] for row in library_shelf(subjects, {}, None)] == ["Mathematics"]
