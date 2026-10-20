from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import StudentPrincipal, library_student
from app.api.responses import LibraryOut
from app.core.errors import ApiError
from app.database.db import AnSession
from app.database.repositories.curriculum import subject_resources_repository
from app.services.cloudinary_delivery import (
    is_authenticated_url,
    sign_delivery_url,
    verify_file_link,
)
from app.services.learning import GetLibraryService

router = APIRouter(prefix="/library", tags=["Student / Library"])

# Headers worth forwarding: enough for pdf.js to range-read the document.
_PASSTHROUGH = ("content-type", "content-length", "content-range", "accept-ranges")


@router.get("", response_model=LibraryOut)
async def library(
    session: AnSession,
    student: Annotated[StudentPrincipal, Depends(library_student)],
    subjectId: str | None = None,
):
    return await GetLibraryService(
        session, student.id, student.tier, student.track, subjectId
    ).process()


@router.get("/file/{resource_id}")
async def library_file(
    resource_id: str, request: Request, session: AnSession, exp: int, sig: str
):
    """Stream a private resource while its short-lived link is still valid.

    The link is issued by the library listing, which has already checked the
    viewer's plan. It carries no bearer token because pdf.js and <a href>
    cannot send one.
    """
    if not verify_file_link(resource_id, exp, sig):
        raise ApiError(403, "This link has expired. Reload to continue.")
    row = await subject_resources_repository.by_id(session, resource_id)
    if row is None or not is_authenticated_url(row.url):
        raise ApiError(404, "Resource not found")

    headers = {"Accept-Encoding": "identity"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=30)
    try:
        upstream = await client.send(
            client.build_request("GET", sign_delivery_url(row.url), headers=headers),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise ApiError(502, "Could not fetch this file") from exc
    if upstream.status_code not in (200, 206):
        await upstream.aclose()
        await client.aclose()
        raise ApiError(502, "Could not fetch this file")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {
        name: upstream.headers[name]
        for name in _PASSTHROUGH
        if name in upstream.headers
    }
    response_headers["Content-Disposition"] = "inline"
    response_headers["Cache-Control"] = "private, no-store"
    return StreamingResponse(
        body(), status_code=upstream.status_code, headers=response_headers
    )
