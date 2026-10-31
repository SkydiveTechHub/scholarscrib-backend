class ApiError(Exception):
    """JSON error body used by almost every route: ``{"error": "..."}``."""

    def __init__(self, status_code: int, error: str, **extra: object) -> None:
        self.status_code = status_code
        self.payload: dict[str, object] = {"error": error, **extra}
        self.retry_after: int | None = None
        super().__init__(error)


class RateLimited(ApiError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            429,
            "Too many requests. Please slow down and try again shortly.",
        )
        self.retry_after = max(1, int(retry_after))


class DatabaseWaking(ApiError):
    """The database is unreachable, typically a scale-to-zero cold start.

    ``code`` lets the client tell this apart from a real failure and retry.
    """

    def __init__(self, retry_after: int = 2) -> None:
        super().__init__(
            503,
            "The database is waking up. Please try again in a moment.",
            code="db_waking",
        )
        self.retry_after = retry_after


def unauthorized() -> ApiError:
    return ApiError(401, "Unauthorized")
