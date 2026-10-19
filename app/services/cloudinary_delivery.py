import base64
import hashlib
import hmac
import re
import time

from app.core.config import settings

# Assets uploaded with type=authenticated have no public URL: Cloudinary only
# serves them when the path carries a valid signature segment.
_AUTHENTICATED = re.compile(
    r"^https://res\.cloudinary\.com/(?P<cloud>[^/]+)/(?P<kind>raw|image)"
    r"/authenticated/(?:s--[^/]+--/)?(?P<version>v\d+/)?(?P<public_id>.+)$"
)


# How long a link issued by the library stays usable. A student who sits on a
# page longer than this gets a reload button instead of a dead document.
FILE_LINK_TTL_SECONDS = 15 * 60


def is_authenticated_url(url: str) -> bool:
    return bool(_AUTHENTICATED.match(url or ""))


def _file_mac(resource_id: str, expires_at: int) -> str:
    message = f"library-file:{resource_id}:{expires_at}".encode()
    digest = hmac.new(settings.auth_secret.encode(), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def file_link(resource_id: str) -> tuple[str, int]:
    """A short-lived path on our own API for one library resource.

    Cloudinary's signed URLs never expire without its token add-on, so the
    expiry lives here: the API streams the file only while the link is fresh.
    """
    expires_at = int(time.time()) + FILE_LINK_TTL_SECONDS
    sig = _file_mac(resource_id, expires_at)
    return f"/api/library/file/{resource_id}?exp={expires_at}&sig={sig}", expires_at


def verify_file_link(resource_id: str, expires_at: int, sig: str) -> bool:
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(_file_mac(resource_id, expires_at), sig)


def sign_delivery_url(url: str) -> str:
    """Re-sign a stored authenticated Cloudinary URL; anything else is returned as-is.

    Used server-side only, when streaming the file. The result never reaches a
    browser. Legacy public ``/upload/`` URLs do not match and pass through
    untouched.
    """
    match = _AUTHENTICATED.match(url or "")
    if not match or not settings.cloudinary_enabled:
        return url
    if match["cloud"] != settings.cloudinary_cloud_name:
        return url
    public_id = match["public_id"]
    digest = hashlib.sha1(
        f"{public_id}{settings.cloudinary_api_secret}".encode()
    ).digest()
    signature = base64.urlsafe_b64encode(digest).decode()[:8]
    return (
        f"https://res.cloudinary.com/{match['cloud']}/{match['kind']}/authenticated"
        f"/s--{signature}--/{match['version'] or ''}{public_id}"
    )
