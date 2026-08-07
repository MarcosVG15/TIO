"""The activity centre.

Derived, not stored. Everything here is computed from rows that already exist -
pending friend requests, friendships that were accepted, posts by people you
follow, a trip about to start - rather than from a notifications table written
at event time.

That choice is deliberate and has a cost worth stating. Derived notifications
cannot be created for things that leave no trace, and they cannot record that a
specific one was dismissed forever. What they buy is that the page works today
against the real database with no migration, and that it can never drift out of
sync with the truth: a friend request that was withdrawn simply stops appearing,
where a stored notification would have to be found and deleted.

Read state lives in memory, keyed by account. It survives the session but not a
restart, which is the honest limit of not having a table. `kind` values are the
five the screen recognises - follow, feature, trip, social, system - anything
else renders as "system".
"""
from __future__ import annotations

import logging
from datetime import date as date_type, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select

from DATABASE.ORM import (
    Account,
    Friendship,
    FriendshipStatus,
    Post,
    Trip,
    TripStatus,
    session_scope,
)
from deps import current_account

log = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])

#: account_id -> set of notification ids the user has marked read, and a flag
#: for "mark everything read" so ids generated later are not retroactively
#: buried. Memory only: see the module note.
_READ: dict[str, set[str]] = {}
_READ_BEFORE: dict[str, datetime] = {}

#: How many to build. The screen paginates by filter, not by page.
MAX_NOTIFICATIONS = 40


def _is_read(account_id: str, notification_id: str, created_at: datetime) -> bool:
    if notification_id in _READ.get(account_id, set()):
        return True
    cutoff = _READ_BEFORE.get(account_id)
    return bool(cutoff and created_at <= cutoff)


def _iso(value: Optional[datetime]) -> str:
    return value.isoformat() if value else datetime.now(timezone.utc).isoformat()


def _build(account: Account) -> list[dict[str, Any]]:
    """Everything worth telling this account about, newest first."""
    items: list[dict[str, Any]] = []
    account_id = str(account.account_id)

    with session_scope() as session:
        # --- people who want to connect ------------------------------------
        incoming = session.scalars(
            select(Friendship).where(
                Friendship.addressee_id == account.account_id,
                Friendship.status == FriendshipStatus.PENDING,
            )
        ).all()
        for row in incoming:
            who = session.get(Account, row.requester_id)
            if who is None:
                continue
            items.append({
                "notification_id": f"friend-req-{row.requester_id}",
                "kind": "follow",
                "title": f"{who.name} wants to connect",
                "body": "Accept to plan trips and chat together.",
                "created_at": _iso(getattr(row, "created_at", None)),
                "action_url": "/socials",
                "action_label": "View request",
                "image_url": getattr(who, "avatar_url", None),
            })

        # --- connections that went through ---------------------------------
        accepted = session.scalars(
            select(Friendship).where(
                or_(
                    Friendship.requester_id == account.account_id,
                    Friendship.addressee_id == account.account_id,
                ),
                Friendship.status == FriendshipStatus.ACCEPTED,
            ).limit(15)
        ).all()
        for row in accepted:
            other_id = (
                row.addressee_id
                if row.requester_id == account.account_id
                else row.requester_id
            )
            who = session.get(Account, other_id)
            if who is None:
                continue
            items.append({
                "notification_id": f"friend-ok-{other_id}",
                "kind": "follow",
                "title": f"You and {who.name} are connected",
                "body": "You can now start a trip chat together.",
                "created_at": _iso(getattr(row, "created_at", None)),
                "action_url": "/chat",
                "action_label": "Open chat",
                "image_url": getattr(who, "avatar_url", None),
            })

        # --- what the people you know have been posting --------------------
        friend_ids = [
            row.addressee_id if row.requester_id == account.account_id
            else row.requester_id
            for row in accepted
        ]
        if friend_ids:
            posts = session.scalars(
                select(Post)
                .where(
                    Post.account_id.in_(friend_ids),
                    Post.deleted_at.is_(None),
                )
                .order_by(Post.created_at.desc())
                .limit(10)
            ).all()
            for post in posts:
                who = session.get(Account, post.account_id)
                items.append({
                    "notification_id": f"post-{post.post_id}",
                    "kind": "social",
                    "title": f"{who.name if who else 'A traveller'} shared a post",
                    "body": (post.body or "")[:140] or "Tap to see it.",
                    "created_at": _iso(post.created_at),
                    "action_url": "/socials",
                    "action_label": "See post",
                    "image_url": getattr(post, "picture_url", None),
                })

        # --- your own trip, when it is close -------------------------------
        today = date_type.today()
        trips = session.scalars(
            select(Trip).where(
                Trip.owner_account_id == account.account_id,
                Trip.deleted_at.is_(None),
                Trip.status.not_in([TripStatus.COMPLETED, TripStatus.CANCELLED]),
                Trip.start_date.is_not(None),
            ).limit(10)
        ).all()
        for trip in trips:
            days_away = (trip.start_date - today).days
            if not 0 <= days_away <= 14:
                continue
            when = (
                "starts today" if days_away == 0
                else "starts tomorrow" if days_away == 1
                else f"starts in {days_away} days"
            )
            items.append({
                "notification_id": f"trip-{trip.trip_id}",
                "kind": "trip",
                "title": f"{trip.name} {when}",
                "body": "Check your timetable and make sure everything is booked.",
                "created_at": _iso(getattr(trip, "updated_at", None)),
                "action_url": "/current-trip",
                "action_label": "Open trip",
                "image_url": None,
            })

    items.sort(key=lambda n: n["created_at"], reverse=True)
    for item in items:
        item["read"] = _is_read(
            account_id, item["notification_id"],
            _parse(item["created_at"]),
        )
    return items[:MAX_NOTIFICATIONS]


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.get("/notifications")
def list_notifications(
    filter: str = Query(default="all"),
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    """Activity for this account.

    `filter` is what the screen's tabs send: all, unread, or a kind. An
    unrecognised value returns everything rather than nothing - a tab the
    backend has not heard of should show too much, not an empty page.
    """
    items = _build(account)
    wanted = (filter or "all").strip().lower()
    if wanted == "unread":
        shown = [n for n in items if not n["read"]]
    elif wanted in {"follow", "feature", "trip", "social", "system"}:
        shown = [n for n in items if n["kind"] == wanted]
    else:
        shown = items

    return {
        "notifications": shown,
        "unread_count": sum(1 for n in items if not n["read"]),
    }


@router.patch("/notifications/{notification_id}")
def mark_notification(
    notification_id: str,
    payload: dict[str, Any],
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    """Mark one read or unread."""
    account_id = str(account.account_id)
    read = bool(payload.get("read", True))
    marks = _READ.setdefault(account_id, set())
    if read:
        marks.add(notification_id)
    else:
        marks.discard(notification_id)

    for item in _build(account):
        if item["notification_id"] == notification_id:
            return {"notification": item}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="notification not found"
    )


@router.post("/notifications/read-all")
def mark_all_read(account: Account = Depends(current_account)) -> dict[str, Any]:
    """Mark everything currently visible as read.

    Recorded as a timestamp rather than by listing ids, so a notification
    derived tomorrow is not born already read.
    """
    account_id = str(account.account_id)
    before = _build(account)
    unread = sum(1 for n in before if not n["read"])
    _READ_BEFORE[account_id] = datetime.now(timezone.utc)
    return {"updated": unread, "unread_count": 0}


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_notification(
    notification_id: str,
    account: Account = Depends(current_account),
) -> None:
    """Dismiss one.

    Dismissing marks it read rather than deleting it: these are derived from
    live rows, so there is nothing to delete, and the underlying fact - a
    pending request, an imminent trip - is still true.
    """
    _READ.setdefault(str(account.account_id), set()).add(notification_id)
    return None
