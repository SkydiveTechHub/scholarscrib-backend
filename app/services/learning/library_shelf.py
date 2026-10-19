def library_shelf(
    subjects: list, counts: dict[str, int], track: str | None
) -> list[dict]:
    """The student's shelf: active CORE subjects plus those of their track."""
    wanted = {"CORE", track.strip().upper()} if track else None
    return [
        {
            "id": subject.id,
            "name": subject.name,
            "slug": subject.slug,
            "code": subject.code,
            "description": getattr(subject, "description", None),
            "trackCategory": subject.track_category,
            "resourceCount": counts.get(subject.id, 0),
        }
        for subject in subjects
        if subject.is_active
        and (wanted is None or subject.track_category in wanted)
    ]
