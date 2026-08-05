from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

import avatars

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.get("/avatars/{filename}")
def get_avatar(filename: str) -> FileResponse:
    """Serve an uploaded profile picture.

    Deliberately unauthenticated - this URL ends up in an `<img src>`, which
    cannot carry a bearer token, and profile pictures are meant to be visible
    to other travellers. `avatars.resolve` refuses any name it did not
    generate, so the path cannot be steered outside the storage directory.
    """
    path = avatars.resolve(filename)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    return FileResponse(
        path,
        media_type=avatars.content_type_of(filename),
        headers={
            # The name contains a hash of the contents, so a given URL can
            # never point at different bytes later - safe to cache hard.
            "Cache-Control": "public, max-age=31536000, immutable",
            # Belt and braces: the bytes were sniffed on upload, but if one
            # ever were mislabelled the browser must not execute it.
            "X-Content-Type-Options": "nosniff",
        },
    )
