"""Friend requests and the friend list.

One row per pair, not two, so "are these two friends" is a single lookup -
at the cost of every query having to check both (requester, addressee)
orderings. `_pair` exists so that condition is written once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, or_, select

from accounts import _person
from DATABASE.ORM import Account, Friendship, FriendshipStatus, session_scope


class FriendError(Exception):
    """Something about the request does not make sense."""


class NotFound(Exception):
    """No such account, or no such request."""


def _as_uuid(value) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _pair(a: UUID, b: UUID):
    """Matches the pair in whichever order it happens to be stored."""
    return or_(
        and_(Friendship.requester_id == a, Friendship.addressee_id == b),
        and_(Friendship.requester_id == b, Friendship.addressee_id == a),
    )


def _live_account(session, account_id: UUID) -> Account:
    account = session.scalar(
        select(Account).where(
            Account.account_id == account_id, Account.deleted_at.is_(None)
        )
    )
    if account is None:
        raise NotFound(str(account_id))
    return account


def friend_ids(session, account_id: UUID) -> set[UUID]:
    """Everyone this account is actually friends with."""
    rows = session.execute(
        select(Friendship.requester_id, Friendship.addressee_id).where(
            Friendship.status == FriendshipStatus.ACCEPTED,
            or_(
                Friendship.requester_id == account_id,
                Friendship.addressee_id == account_id,
            ),
        )
    ).all()
    return {
        (r.addressee_id if r.requester_id == account_id else r.requester_id)
        for r in rows
    }


def pending_ids(session, account_id: UUID) -> set[UUID]:
    """Everyone with an outstanding request in either direction.

    Not friends yet, but not strangers either - one of them has asked. Chat
    treats that as enough, because the alternative is a social flow whose only
    button leads somewhere that refuses you.
    """
    rows = session.execute(
        select(Friendship.requester_id, Friendship.addressee_id).where(
            Friendship.status == FriendshipStatus.PENDING,
            or_(
                Friendship.requester_id == account_id,
                Friendship.addressee_id == account_id,
            ),
        )
    ).all()
    return {
        (r.addressee_id if r.requester_id == account_id else r.requester_id)
        for r in rows
    }


def are_friends(session, a: UUID, b: UUID) -> bool:
    return (
        session.scalar(
            select(Friendship).where(
                _pair(a, b), Friendship.status == FriendshipStatus.ACCEPTED
            )
        )
        is not None
    )


def send_request(account_id: UUID, other: str) -> dict[str, Any]:
    other_id = _as_uuid(other)
    if other_id is None:
        raise NotFound(str(other))
    if other_id == account_id:
        raise FriendError("You cannot add yourself.")

    with session_scope() as session:
        target = _live_account(session, other_id)
        existing = session.scalar(select(Friendship).where(_pair(account_id, other_id)))

        if existing is not None:
            if existing.status == FriendshipStatus.ACCEPTED:
                raise FriendError("You are already friends.")
            if existing.status == FriendshipStatus.BLOCKED:
                raise FriendError("This request cannot be sent.")
            if existing.status == FriendshipStatus.PENDING:
                if existing.addressee_id == account_id:
                    # They asked first - treat this as accepting, which is
                    # what the person plainly means.
                    existing.status = FriendshipStatus.ACCEPTED
                    existing.responded_at = datetime.now(timezone.utc)
                    return {"status": "accepted", "person": _person(target)}
                raise FriendError("Request already sent.")
            # Previously declined: let them try again.
            existing.requester_id = account_id
            existing.addressee_id = other_id
            existing.status = FriendshipStatus.PENDING
            existing.responded_at = None
            return {"status": "pending", "person": _person(target)}

        session.add(
            Friendship(
                requester_id=account_id,
                addressee_id=other_id,
                status=FriendshipStatus.PENDING,
            )
        )
        session.flush()
        return {"status": "pending", "person": _person(target)}


def respond(account_id: UUID, requester: str, accept: bool) -> dict[str, Any]:
    """Accept or decline. Only the addressee may answer."""
    requester_id = _as_uuid(requester)
    if requester_id is None:
        raise NotFound(str(requester))

    with session_scope() as session:
        request = session.scalar(
            select(Friendship).where(
                Friendship.requester_id == requester_id,
                Friendship.addressee_id == account_id,
                Friendship.status == FriendshipStatus.PENDING,
            )
        )
        if request is None:
            raise NotFound("no pending request from that account")

        request.status = (
            FriendshipStatus.ACCEPTED if accept else FriendshipStatus.DECLINED
        )
        request.responded_at = datetime.now(timezone.utc)
        person = session.get(Account, requester_id)
        return {
            "status": request.status.value,
            "person": _person(person) if person else None,
        }


def list_requests(account_id: UUID) -> dict[str, list[dict]]:
    """Pending requests, split by direction."""
    with session_scope() as session:
        incoming_rows = session.execute(
            select(Account, Friendship.created_at)
            .join(Friendship, Friendship.requester_id == Account.account_id)
            .where(
                Friendship.addressee_id == account_id,
                Friendship.status == FriendshipStatus.PENDING,
                Account.deleted_at.is_(None),
            )
            .order_by(Friendship.created_at.desc())
        ).all()
        outgoing_rows = session.execute(
            select(Account, Friendship.created_at)
            .join(Friendship, Friendship.addressee_id == Account.account_id)
            .where(
                Friendship.requester_id == account_id,
                Friendship.status == FriendshipStatus.PENDING,
                Account.deleted_at.is_(None),
            )
            .order_by(Friendship.created_at.desc())
        ).all()
        return {
            "incoming": [_person(a) for a, _ in incoming_rows],
            "outgoing": [_person(a) for a, _ in outgoing_rows],
        }


def list_friends(account_id: UUID) -> list[dict]:
    with session_scope() as session:
        ids = friend_ids(session, account_id)
        if not ids:
            return []
        rows = session.scalars(
            select(Account)
            .where(Account.account_id.in_(ids), Account.deleted_at.is_(None))
            .order_by(Account.name)
        ).all()
        return [_person(a) for a in rows]


def remove_friend(account_id: UUID, other: str) -> None:
    """Unfriend, or withdraw a request. Deletes the row outright - keeping a
    'declined' tombstone would silently block them from ever asking again.
    """
    other_id = _as_uuid(other)
    if other_id is None:
        raise NotFound(str(other))

    with session_scope() as session:
        row = session.scalar(select(Friendship).where(_pair(account_id, other_id)))
        if row is None:
            raise NotFound("not connected to that account")
        session.delete(row)
