"""Avatar image storage.

Profile pictures are rows in `avatars`, bytes included - see the model's
docstring for why they are in the database rather than on a disk. This module
is the only thing that reads or writes them.

`Account.avatar_url` holds a URL either way, so nothing downstream needs to
know whether a picture was uploaded or linked from Google.

The serving URLs are public and unauthenticated. They have to be: a browser
cannot attach a bearer token to an `<img src>`, and travellers need to load
each other's pictures. A random UUID per row is what keeps them unguessable,
so treat them as unlisted rather than secret.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple, Optional
from uuid import UUID

from sqlalchemy import select

from DATABASE.ORM import Account, Avatar, session_scope

#: Served under /api so the existing reverse-proxy rule already routes it -
#: Caddy sends /api/* to the API and everything else to the static frontend.
URL_PREFIX = "/api/uploads/avatars"

#: 5 MB. Large enough for a photo straight off a phone, small enough to sit in
#: a row comfortably. Mirrored by a CHECK constraint on the table.
MAX_BYTES = 5 * 1024 * 1024

#: Content type is sniffed from the bytes, never taken from the request - a
#: client can claim any Content-Type it likes.
_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("image/jpeg", b"\xff\xd8\xff"),
    ("image/png", b"\x89PNG\r\n\x1a\n"),
    ("image/gif", b"GIF87a"),
    ("image/gif", b"GIF89a"),
)


class RejectedImage(Exception):
    """The bytes are not a supported image, or there are too many of them."""


class StoredAvatar(NamedTuple):
    """Everything needed to serve one picture back."""

    data: bytes
    content_type: str
    sha256: str


def _sniff(data: bytes) -> str:
    """Return the content type, or raise RejectedImage."""
    for content_type, magic in _SIGNATURES:
        if data.startswith(magic):
            return content_type

    # WEBP is RIFF-framed: "RIFF" <4 byte length> "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"

    raise RejectedImage("That file is not a JPEG, PNG, GIF or WebP image.")


def url_for(avatar_id: UUID) -> str:
    return f"{URL_PREFIX}/{avatar_id}"


def id_from_url(url: Optional[str]) -> Optional[UUID]:
    """The avatar id inside one of our URLs, or None for anything else - a
    Google or Unsplash link has no row of ours behind it.
    """
    if not url or not url.startswith(f"{URL_PREFIX}/"):
        return None
    try:
        return UUID(url[len(URL_PREFIX) + 1 :])
    except ValueError:
        return None


def store(account_id: UUID, data: bytes) -> str:
    """Validate and save an image; return the URL that serves it.

    Older uploads by this account are deleted, except the one their profile
    currently points at - a user who drops five photos while deciding should
    not leave five rows behind, but the picture they already saved has to keep
    working if they never press Save again.
    """
    if not data:
        raise RejectedImage("That file is empty.")
    if len(data) > MAX_BYTES:
        raise RejectedImage(
            f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB."
        )

    content_type = _sniff(data)
    digest = hashlib.sha256(data).hexdigest()

    with session_scope() as session:
        # Read the saved URL inside the transaction rather than trusting a
        # value the caller loaded earlier: a second tab may have saved since.
        saved_url = session.scalar(
            select(Account.avatar_url).where(Account.account_id == account_id)
        )
        keep = id_from_url(saved_url)

        # The unique constraint on (account_id, sha256) makes this idempotent -
        # the same file twice is the same row and the same URL.
        avatar = session.scalar(
            select(Avatar).where(
                Avatar.account_id == account_id, Avatar.sha256 == digest
            )
        )
        if avatar is None:
            avatar = Avatar(
                account_id=account_id,
                content_type=content_type,
                sha256=digest,
                byte_size=len(data),
                data=data,
            )
            session.add(avatar)
            session.flush()  # assigns avatar_id before it is used below

        superseded = session.scalars(
            select(Avatar).where(
                Avatar.account_id == account_id,
                Avatar.avatar_id != avatar.avatar_id,
                *([Avatar.avatar_id != keep] if keep is not None else []),
            )
        ).all()
        for row in superseded:
            session.delete(row)

        return url_for(avatar.avatar_id)


def fetch(avatar_id: UUID) -> Optional[StoredAvatar]:
    """One stored picture, or None if there is no such row."""
    with session_scope() as session:
        avatar = session.get(Avatar, avatar_id)
        if avatar is None:
            return None
        return StoredAvatar(avatar.data, avatar.content_type, avatar.sha256)
