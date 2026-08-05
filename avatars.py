"""Avatar image storage.

Profile pictures are files, not rows, so they live on disk and the database
stores only the URL that serves them back - the same shape as an external
`https://lh3.googleusercontent.com/...` link, so `Account.avatar_url` does not
need to know whether a picture was uploaded or pasted.

Uploaded files are content-addressed: the name is the account id plus a hash
of the bytes. Two consequences worth relying on - dropping the same photo
twice is idempotent rather than leaving a duplicate, and a name can never be
guessed from the account id alone.

The served URLs are public and unauthenticated. They have to be: a browser
cannot attach a bearer token to an `<img src>`, and other travellers need to
load each other's pictures. The hash in the name is what keeps them from being
enumerable, so treat these as unlisted rather than secret.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional
from uuid import UUID

#: Where the files live. Repo-relative in development; a mounted volume in the
#: container, because the image filesystem does not survive a rebuild.
STORAGE_DIR = Path(os.getenv("AVATAR_DIR", "uploads/avatars"))

#: Served under /api so the existing reverse-proxy rule already routes it -
#: Caddy sends /api/* to the API and everything else to the static frontend.
URL_PREFIX = "/api/uploads/avatars"

#: 5 MB. Large enough for a photo straight off a phone, small enough that a
#: careless upload cannot fill the volume.
MAX_BYTES = 5 * 1024 * 1024

#: Content type is sniffed from the bytes, never taken from the request - a
#: client can claim any Content-Type it likes.
_SIGNATURES: tuple[tuple[str, str, bytes], ...] = (
    ("image/jpeg", ".jpg", b"\xff\xd8\xff"),
    ("image/png", ".png", b"\x89PNG\r\n\x1a\n"),
    ("image/gif", ".gif", b"GIF87a"),
    ("image/gif", ".gif", b"GIF89a"),
)

#: Only ever matched against names this module generated, so it doubles as the
#: path-traversal guard in `resolve`.
_FILENAME = re.compile(r"\A[0-9a-f]{32}-[0-9a-f]{16}\.(jpg|png|gif|webp)\Z")


class RejectedImage(Exception):
    """The bytes are not a supported image, or there are too many of them."""


def _sniff(data: bytes) -> tuple[str, str]:
    """Return (content_type, extension) or raise RejectedImage."""
    for content_type, extension, magic in _SIGNATURES:
        if data.startswith(magic):
            return content_type, extension

    # WEBP is RIFF-framed: "RIFF" <4 byte length> "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"

    raise RejectedImage("That file is not a JPEG, PNG, GIF or WebP image.")


def content_type_of(filename: str) -> str:
    """The type to serve a stored file as.

    Derived from our own extension rather than sniffed again - the file was
    already validated on the way in.
    """
    suffix = Path(filename).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def store(account_id: UUID, data: bytes, *, keep: Optional[str] = None) -> str:
    """Validate and save an image; return the URL that serves it.

    `keep` is the URL currently persisted on the account. Older uploads by the
    same account are deleted, except that one - a user who drops five photos
    while deciding should not leave five files behind, but the picture they
    already saved has to keep working if they never press Save again.
    """
    if not data:
        raise RejectedImage("That file is empty.")
    if len(data) > MAX_BYTES:
        raise RejectedImage(
            f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB."
        )

    _, extension = _sniff(data)

    # The client's filename is never used: it is attacker-controlled and would
    # be a path-traversal vector.
    digest = hashlib.sha256(data).hexdigest()[:16]
    filename = f"{account_id.hex}-{digest}{extension}"

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    destination = STORAGE_DIR / filename

    if not destination.exists():
        # Write to a temporary name and rename, so a failed or concurrent
        # write can never leave a truncated image at the real path. Rename is
        # atomic within a filesystem.
        staging = destination.with_suffix(destination.suffix + ".part")
        staging.write_bytes(data)
        os.replace(staging, destination)

    _prune(account_id, keep={filename, _basename(keep)})
    return f"{URL_PREFIX}/{filename}"


def resolve(filename: str) -> Optional[Path]:
    """The path a stored avatar lives at, or None if the name is not one we
    could have produced.

    Rejecting on the pattern is what makes traversal impossible - "..", a
    slash and a backslash all fail to match before touching the filesystem.
    """
    if not _FILENAME.match(filename):
        return None
    path = STORAGE_DIR / filename
    return path if path.is_file() else None


def _basename(url: Optional[str]) -> Optional[str]:
    """The stored filename inside one of our URLs, or None for anything else -
    a Google or Unsplash link has no file of ours to protect.
    """
    if not url or not url.startswith(f"{URL_PREFIX}/"):
        return None
    return url[len(URL_PREFIX) + 1 :]


def _prune(account_id: UUID, keep: set[Optional[str]]) -> None:
    """Delete this account's other uploads. Best effort: a file that will not
    unlink is wasted disk, not a failed upload, so it must not raise.
    """
    if not STORAGE_DIR.is_dir():
        return
    for path in STORAGE_DIR.glob(f"{account_id.hex}-*"):
        if path.name in keep:
            continue
        try:
            path.unlink()
        except OSError:
            pass
