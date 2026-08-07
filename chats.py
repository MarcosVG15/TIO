"""Group chat.

Membership follows the ERD: a Conversation is paired with a Group, and the
people in it are that Group's GroupMembers. So creating a chat creates both
rows, and every read is authorised by checking GroupMember.

The wire format is camelCase (see schemas.py) because that is what the chat
screen reads; everything below stays snake_case and the schemas translate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select

from friends import friend_ids, pending_ids
from DATABASE.ORM import (
    Account,
    Conversation,
    Group,
    GroupMember,
    GroupRole,
    Message,
    MessageType,
    session_scope,
)


class ChatNotFound(Exception):
    """No such conversation, or the caller is not in it.

    One exception for both so a caller cannot probe which chat ids exist.
    """


class InvalidMembers(Exception):
    """One or more member ids do not resolve to a live account."""


class NotFriends(Exception):
    """You can only put people you are friends with into a chat."""


def _as_uuid(value: str) -> Optional[UUID]:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _check_all_friends(session, account_id: UUID, wanted: set[UUID]) -> None:
    """Every person being added must be connected, or asked to connect.

    A pending request counts. Requiring an *accepted* friendship made the whole
    social flow a dead end: the screen's only action is "Follow", which sends a
    request, and starting a chat then failed until the other person happened to
    log in and accept - which in a demo, or with a friend sitting next to you,
    never happens. Worse, the chat screen renders any failure as "server
    unreachable", so the refusal did not even read as a rule.

    Someone you have reached out to is someone you meant to talk to. They can
    still ignore the chat, exactly as they can ignore the request.

    Checked against the whole set in one query rather than per person, and the
    message names who is missing so the UI can say something useful.
    """
    if not wanted:
        return
    mine = friend_ids(session, account_id) | pending_ids(session, account_id)
    strangers = wanted - mine
    if strangers:
        names = session.scalars(
            select(Account.name).where(Account.account_id.in_(strangers))
        ).all()
        raise NotFriends(
            ", ".join(names) if names else ", ".join(str(s) for s in strangers)
        )


def _membership(session, account_id: UUID, chat_id: UUID) -> GroupMember:
    """The caller's membership row, or ChatNotFound."""
    membership = session.scalar(
        select(GroupMember)
        .join(Group, Group.group_id == GroupMember.group_id)
        .where(
            Group.chat_id == chat_id,
            GroupMember.account_id == account_id,
            Group.deleted_at.is_(None),
        )
    )
    if membership is None:
        raise ChatNotFound(str(chat_id))
    return membership


def _members_of(session, group_id: UUID) -> list[dict[str, str]]:
    rows = session.execute(
        select(Account.account_id, Account.name)
        .join(GroupMember, GroupMember.account_id == Account.account_id)
        .where(GroupMember.group_id == group_id)
        .order_by(Account.name)
    ).all()
    return [{"id": str(r.account_id), "name": r.name} for r in rows]


def _conversation_payload(
    session, group: Group, membership: GroupMember
) -> dict[str, Any]:
    last = session.scalar(
        select(Message)
        .where(Message.chat_id == group.chat_id)
        .order_by(Message.sent_at.desc())
        .limit(1)
    )

    unread_q = select(func.count(Message.message_id)).where(
        Message.chat_id == group.chat_id,
        # Your own messages are never unread.
        or_(Message.sender_id.is_(None), Message.sender_id != membership.account_id),
    )
    if membership.last_read_at is not None:
        unread_q = unread_q.where(Message.sent_at > membership.last_read_at)
    unread = session.scalar(unread_q) or 0

    members = _members_of(session, group.group_id)
    return {
        "id": str(group.chat_id),
        "name": group.group_name,
        "is_group": len(members) > 2,
        "members": members,
        "last_message": last.message_text if last else None,
        "unread_count": int(unread),
    }


def list_conversations(account_id: UUID) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(
            select(Group, GroupMember)
            .join(GroupMember, GroupMember.group_id == Group.group_id)
            .where(
                GroupMember.account_id == account_id,
                Group.deleted_at.is_(None),
                Group.chat_id.is_not(None),
            )
            .order_by(Group.created_at.desc())
        ).all()
        return [_conversation_payload(session, g, m) for g, m in rows]


def create_conversation(
    account_id: UUID,
    name: Optional[str],
    member_ids: list[str],
    is_group: bool = False,
) -> dict[str, Any]:
    """Create a Conversation plus its backing Group, with the caller as owner."""
    with session_scope() as session:
        wanted = {u for u in (_as_uuid(m) for m in member_ids) if u is not None}
        wanted.discard(account_id)

        if wanted:
            found = set(
                session.scalars(
                    select(Account.account_id).where(
                        Account.account_id.in_(wanted),
                        Account.deleted_at.is_(None),
                    )
                ).all()
            )
            missing = wanted - found
            if missing:
                raise InvalidMembers(", ".join(str(m) for m in missing))

            _check_all_friends(session, account_id, wanted)

        conversation = Conversation()
        session.add(conversation)
        session.flush()

        group = Group(
            group_name=(name or "").strip() or "New chat",
            chat_id=conversation.chat_id,
        )
        session.add(group)
        session.flush()

        session.add(
            GroupMember(
                group_id=group.group_id,
                account_id=account_id,
                role=GroupRole.OWNER,
            )
        )
        for member_id in wanted:
            session.add(
                GroupMember(group_id=group.group_id, account_id=member_id)
            )
        session.flush()

        membership = _membership(session, account_id, conversation.chat_id)
        return _conversation_payload(session, group, membership)


def list_messages(account_id: UUID, chat_id: str) -> list[dict[str, Any]]:
    """Messages oldest-first. Reading also marks the chat as read."""
    cid = _as_uuid(chat_id)
    if cid is None:
        raise ChatNotFound(chat_id)

    with session_scope() as session:
        membership = _membership(session, account_id, cid)

        rows = session.execute(
            select(Message, Account.name)
            .outerjoin(Account, Account.account_id == Message.sender_id)
            .where(Message.chat_id == cid)
            .order_by(Message.sent_at)
        ).all()

        # Opening the thread is what clears the badge.
        membership.last_read_at = datetime.now(timezone.utc)

        return [
            {
                "id": str(msg.message_id),
                "body": msg.message_text or "",
                "author_name": author or "TIO",
                "mine": msg.sender_id == account_id,
                "created_at": msg.sent_at,
            }
            for msg, author in rows
        ]


def send_message(account_id: UUID, chat_id: str, body: str) -> dict[str, Any]:
    cid = _as_uuid(chat_id)
    if cid is None:
        raise ChatNotFound(chat_id)

    with session_scope() as session:
        membership = _membership(session, account_id, cid)
        sender = session.get(Account, account_id)

        message = Message(
            chat_id=cid,
            sender_id=account_id,
            message_text=body.strip(),
            message_type=MessageType.TEXT,
        )
        session.add(message)
        # Sending counts as reading, or your own message would come back unread.
        membership.last_read_at = datetime.now(timezone.utc)
        session.flush()

        return {
            "id": str(message.message_id),
            "body": message.message_text,
            "author_name": sender.name if sender else "TIO",
            "mine": True,
            "created_at": message.sent_at or datetime.now(timezone.utc),
        }


def leave_conversation(account_id: UUID, chat_id: str) -> None:
    """Remove the caller from a chat.

    The messages stay - other members would otherwise see gaps in a thread
    they were part of. Once the last person leaves, the group is soft-deleted
    so it stops appearing anywhere.
    """
    cid = _as_uuid(chat_id)
    if cid is None:
        raise ChatNotFound(chat_id)

    with session_scope() as session:
        membership = _membership(session, account_id, cid)
        group_id = membership.group_id
        session.delete(membership)
        session.flush()

        remaining = session.scalar(
            select(func.count(GroupMember.group_member_id)).where(
                GroupMember.group_id == group_id
            )
        )
        if not remaining:
            group = session.get(Group, group_id)
            if group is not None:
                group.deleted_at = datetime.now(timezone.utc)


def add_members(
    account_id: UUID, chat_id: str, member_ids: list[str]
) -> dict[str, Any]:
    cid = _as_uuid(chat_id)
    if cid is None:
        raise ChatNotFound(chat_id)

    with session_scope() as session:
        _membership(session, account_id, cid)

        group = session.scalar(select(Group).where(Group.chat_id == cid))
        if group is None:
            raise ChatNotFound(chat_id)

        wanted = {u for u in (_as_uuid(m) for m in member_ids) if u is not None}
        existing = set(
            session.scalars(
                select(GroupMember.account_id).where(
                    GroupMember.group_id == group.group_id
                )
            ).all()
        )
        to_add = wanted - existing
        if to_add:
            found = set(
                session.scalars(
                    select(Account.account_id).where(
                        Account.account_id.in_(to_add),
                        Account.deleted_at.is_(None),
                    )
                ).all()
            )
            missing = to_add - found
            if missing:
                raise InvalidMembers(", ".join(str(m) for m in missing))

            _check_all_friends(session, account_id, to_add)

            for member_id in to_add:
                session.add(
                    GroupMember(group_id=group.group_id, account_id=member_id)
                )
            session.flush()

        membership = _membership(session, account_id, cid)
        return _conversation_payload(session, group, membership)
