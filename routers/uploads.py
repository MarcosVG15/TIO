from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, status

import avatars

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get(
    "/avatars/{avatar_id}",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}}},
)
def get_avatar(avatar_id: str, request: Request) -> Response:
    """Serve an uploaded profile picture.

    Deliberately unauthenticated - this URL ends up in an `<img src>`, which
    cannot carry a bearer token, and profile pictures are meant to be visible
    to other travellers.

    A malformed id is a 404 rather than a 422: the path is a URL somebody's
    browser is loading, not an API call, and "no such picture" is the honest
    answer either way.
    """
    try:
        ident = UUID(avatar_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    stored = avatars.fetch(ident)
    if stored is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    etag = f'"{stored.sha256}"'
    headers = {
        "ETag": etag,
        # A row's bytes never change - a different picture is a different id -
        # so this URL can be cached hard.
        "Cache-Control": "public, max-age=31536000, immutable",
        # Belt and braces: the bytes were sniffed on upload, but if one ever
        # were mislabelled the browser must not execute it.
        "X-Content-Type-Options": "nosniff",
    }

    # Spare the bytes when the browser already has them. Cheap to honour and
    # the common case once a profile screen has been visited twice.
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    return Response(content=stored.data, media_type=stored.content_type, headers=headers)
