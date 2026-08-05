"""Social feed, likes, comments and connections.

Following was folded into friendship - there is one relationship, not two.
Feed visibility is therefore: your own posts, your friends' posts, and
anything public. No ranking, newest first.

Wire format is camelCase, matching the chat and people endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select

from DATABASE.ORM import (
    Account,
    Friendship,
    FriendshipStatus,
    Location,
    Post,
    PostComment,
    PostLike,
    PostVisibility,
    session_scope,
)
from friends import friend_ids


class PostNotFound(Exception):
    pass


class NotFound(Exception):
    pass


class InvalidPost(Exception):
    pass


def _as_uuid(value) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _post_payload(post: Post, author: Account, location: Optional[Location],
                  likes: int, liked: bool, comments: int = 0) -> dict[str, Any]:
    place = None
    if location is not None:
        place = ", ".join(p for p in (location.name, location.country) if p)

    return {
        "id": str(post.post_id),
        "author": {
            "id": str(author.account_id),
            "name": author.name,
            "avatarUrl": (author.account_metadata or {}).get("picture"),
        },
        "location": place,
        "rating": post.rating,
        "imageUrl": post.media_url,
        "caption": post.caption or "",
        "likes": likes,
        "comments": comments,
        "liked": liked,
        "createdAt": post.created_at,
    }


def feed(account_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        # Friendship is the only relationship now - following was folded into
        # it, so there is one notion of "people I'm connected to".
        visible_to_me = friend_ids(session, account_id)
        visible_to_me.add(account_id)

        rows = session.execute(
            select(Post, Account, Location)
            .join(Account, Account.account_id == Post.account_id)
            .outerjoin(Location, Location.location_id == Post.location_id)
            .where(
                Post.deleted_at.is_(None),
                Account.deleted_at.is_(None),
                or_(
                    Post.account_id.in_(visible_to_me),
                    Post.visibility == PostVisibility.PUBLIC,
                ),
            )
            .order_by(Post.created_at.desc())
            .limit(limit)
        ).all()

        post_ids = [p.post_id for p, _, _ in rows]
        likes: dict[UUID, int] = {}
        comments: dict[UUID, int] = {}
        mine: set[UUID] = set()
        if post_ids:
            # Three aggregate queries for the whole page rather than three
            # per post, which is the difference between 3 queries and 150.
            likes = {
                pid: int(n)
                for pid, n in session.execute(
                    select(PostLike.post_id, func.count(PostLike.account_id))
                    .where(PostLike.post_id.in_(post_ids))
                    .group_by(PostLike.post_id)
                ).all()
            }
            comments = {
                pid: int(n)
                for pid, n in session.execute(
                    select(PostComment.post_id, func.count(PostComment.comment_id))
                    .where(PostComment.post_id.in_(post_ids))
                    .group_by(PostComment.post_id)
                ).all()
            }
            mine = set(
                session.scalars(
                    select(PostLike.post_id).where(
                        PostLike.post_id.in_(post_ids),
                        PostLike.account_id == account_id,
                    )
                ).all()
            )

        return [
            _post_payload(
                p,
                a,
                loc,
                likes.get(p.post_id, 0),
                p.post_id in mine,
                comments.get(p.post_id, 0),
            )
            for p, a, loc in rows
        ]


def create_post(
    account_id: UUID,
    caption: Optional[str],
    image_url: Optional[str] = None,
    trip_id: Optional[str] = None,
    location_id: Optional[str] = None,
    rating: Optional[int] = None,
    visibility: PostVisibility = PostVisibility.PUBLIC,
) -> dict[str, Any]:
    caption = (caption or "").strip() or None
    if not caption and not image_url:
        # Mirrors the database check constraint, so the caller gets a clear
        # message instead of an IntegrityError.
        raise InvalidPost("A post needs a caption or an image.")

    with session_scope() as session:
        post = Post(
            account_id=account_id,
            caption=caption,
            media_url=image_url,
            trip_id=_as_uuid(trip_id) if trip_id else None,
            location_id=_as_uuid(location_id) if location_id else None,
            rating=rating,
            visibility=visibility,
        )
        session.add(post)
        session.flush()

        author = session.get(Account, account_id)
        location = (
            session.get(Location, post.location_id) if post.location_id else None
        )
        return _post_payload(post, author, location, 0, False)


def toggle_like(account_id: UUID, post_id: str) -> dict[str, Any]:
    """One endpoint for like and unlike - the UI sends the same POST either
    way, so the server decides based on current state.
    """
    pid = _as_uuid(post_id)
    if pid is None:
        raise PostNotFound(post_id)

    with session_scope() as session:
        post = session.scalar(
            select(Post).where(Post.post_id == pid, Post.deleted_at.is_(None))
        )
        if post is None:
            raise PostNotFound(post_id)

        existing = session.get(PostLike, {"post_id": pid, "account_id": account_id})
        if existing is not None:
            session.delete(existing)
            liked = False
        else:
            session.add(PostLike(post_id=pid, account_id=account_id))
            liked = True
        session.flush()

        likes = session.scalar(
            select(func.count(PostLike.account_id)).where(PostLike.post_id == pid)
        )
        return {"id": str(pid), "liked": liked, "likes": int(likes or 0)}


def _comment_payload(comment: PostComment, author: Account) -> dict[str, Any]:
    return {
        "id": str(comment.comment_id),
        "body": comment.body,
        "author": {
            "id": str(author.account_id),
            "name": author.name,
            "avatarUrl": (author.account_metadata or {}).get("picture"),
        },
        "mine": False,
        "createdAt": comment.created_at,
    }


def list_comments(account_id: UUID, post_id: str) -> list[dict[str, Any]]:
    pid = _as_uuid(post_id)
    if pid is None:
        raise PostNotFound(post_id)

    with session_scope() as session:
        post = session.scalar(
            select(Post).where(Post.post_id == pid, Post.deleted_at.is_(None))
        )
        if post is None:
            raise PostNotFound(post_id)

        rows = session.execute(
            select(PostComment, Account)
            .join(Account, Account.account_id == PostComment.account_id)
            .where(PostComment.post_id == pid)
            .order_by(PostComment.created_at)
        ).all()
        out = []
        for comment, author in rows:
            payload = _comment_payload(comment, author)
            payload["mine"] = comment.account_id == account_id
            out.append(payload)
        return out


def add_comment(account_id: UUID, post_id: str, body: str) -> dict[str, Any]:
    pid = _as_uuid(post_id)
    if pid is None:
        raise PostNotFound(post_id)

    text = (body or "").strip()
    if not text:
        raise InvalidPost("A comment cannot be empty.")

    with session_scope() as session:
        post = session.scalar(
            select(Post).where(Post.post_id == pid, Post.deleted_at.is_(None))
        )
        if post is None:
            raise PostNotFound(post_id)

        comment = PostComment(post_id=pid, account_id=account_id, body=text)
        session.add(comment)
        session.flush()

        author = session.get(Account, account_id)
        payload = _comment_payload(comment, author)
        payload["mine"] = True
        return payload


def delete_comment(account_id: UUID, comment_id: str) -> None:
    """Only the comment's author may remove it.

    A missing comment and someone else's comment both return NotFound, so a
    caller cannot probe which comment ids exist.
    """
    cid = _as_uuid(comment_id)
    if cid is None:
        raise NotFound(comment_id)

    with session_scope() as session:
        comment = session.scalar(
            select(PostComment).where(
                PostComment.comment_id == cid,
                PostComment.account_id == account_id,
            )
        )
        if comment is None:
            raise NotFound(comment_id)
        session.delete(comment)


def toggle_connection(account_id: UUID, other: str) -> dict[str, Any]:
    """Follow is friendship.

    There is no separate one-way follow any more, so this sends a friend
    request, accepts a pending one from them, or disconnects if already
    connected. The response keeps a `following` boolean because the existing
    UI reads it, but it now means "connected or requested".
    """
    other_id = _as_uuid(other)
    if other_id is None:
        raise NotFound(str(other))
    if other_id == account_id:
        raise InvalidPost("You cannot connect to yourself.")

    with session_scope() as session:
        target = session.scalar(
            select(Account).where(
                Account.account_id == other_id, Account.deleted_at.is_(None)
            )
        )
        if target is None:
            raise NotFound(str(other))

        existing = session.scalar(
            select(Friendship).where(
                or_(
                    and_(
                        Friendship.requester_id == account_id,
                        Friendship.addressee_id == other_id,
                    ),
                    and_(
                        Friendship.requester_id == other_id,
                        Friendship.addressee_id == account_id,
                    ),
                )
            )
        )

        if existing is None:
            session.add(
                Friendship(
                    requester_id=account_id,
                    addressee_id=other_id,
                    status=FriendshipStatus.PENDING,
                )
            )
            state, following = "pending_out", True
        elif existing.status == FriendshipStatus.PENDING:
            if existing.addressee_id == account_id:
                # They asked first - pressing the button means "yes".
                existing.status = FriendshipStatus.ACCEPTED
                existing.responded_at = datetime.now(timezone.utc)
                state, following = "friends", True
            else:
                # Pressing it again withdraws the request.
                session.delete(existing)
                state, following = None, False
        elif existing.status == FriendshipStatus.ACCEPTED:
            session.delete(existing)
            state, following = None, False
        else:
            existing.requester_id = account_id
            existing.addressee_id = other_id
            existing.status = FriendshipStatus.PENDING
            existing.responded_at = None
            state, following = "pending_out", True

        session.flush()
        return {
            "id": str(other_id),
            "friendshipStatus": state,
            "following": following,
        }
