"""Group chat endpoints.

Backed by the Conversation / Message / Group / GroupMember tables. Responses
are camelCase - see the Chat section of schemas.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import chats
from DATABASE.ORM import Account
from deps import current_account
from schemas import (
    ConversationCreate,
    ConversationListOut,
    ConversationOut,
    MembersAdd,
    MessageCreate,
    MessageListOut,
    MessageOut,
)

router = APIRouter(tags=["conversations"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
    )


def _not_friends(exc: Exception) -> HTTPException:
    # The names are safe to echo: the caller supplied those account ids, and
    # names are already visible through people search.
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You can only add friends to a chat. Send a request to: {exc}",
    )


@router.get("/conversations", response_model=ConversationListOut)
def list_conversations(account: Account = Depends(current_account)):
    """Every conversation the caller belongs to, newest first.

    Empty list when they have none - a normal state, not an error.
    """
    return {"conversations": chats.list_conversations(account.account_id)}


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: ConversationCreate,
    account: Account = Depends(current_account),
):
    """Start a conversation. The caller is added as owner automatically, so
    memberIds only needs the other people.
    """
    try:
        return chats.create_conversation(
            account_id=account.account_id,
            name=payload.name,
            member_ids=payload.member_ids,
            is_group=payload.is_group,
        )
    except chats.NotFriends as exc:
        raise _not_friends(exc) from exc
    except chats.InvalidMembers as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown member: {exc}",
        ) from exc


@router.get("/conversations/{chat_id}/messages", response_model=MessageListOut)
def list_messages(chat_id: str, account: Account = Depends(current_account)):
    """Messages oldest-first. Fetching also marks the chat as read, which is
    what clears the unread badge.
    """
    try:
        return {"messages": chats.list_messages(account.account_id, chat_id)}
    except chats.ChatNotFound as exc:
        raise _not_found() from exc


@router.post(
    "/conversations/{chat_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    chat_id: str,
    payload: MessageCreate,
    account: Account = Depends(current_account),
):
    try:
        return chats.send_message(account.account_id, chat_id, payload.body)
    except chats.ChatNotFound as exc:
        raise _not_found() from exc


@router.delete(
    "/conversations/{chat_id}", status_code=status.HTTP_204_NO_CONTENT
)
def leave_conversation(
    chat_id: str, account: Account = Depends(current_account)
) -> None:
    """Leave a chat.

    Messages stay for everyone else - removing them would leave gaps in a
    thread other people were part of. The group disappears entirely once the
    last member leaves.
    """
    try:
        chats.leave_conversation(account.account_id, chat_id)
    except chats.ChatNotFound as exc:
        raise _not_found() from exc


@router.post("/conversations/{chat_id}/members", response_model=ConversationOut)
def add_members(
    chat_id: str,
    payload: MembersAdd,
    account: Account = Depends(current_account),
):
    """Add people. Already-present members are ignored rather than erroring."""
    try:
        return chats.add_members(
            account.account_id, chat_id, payload.member_ids
        )
    except chats.ChatNotFound as exc:
        raise _not_found() from exc
    except chats.NotFriends as exc:
        raise _not_friends(exc) from exc
    except chats.InvalidMembers as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown member: {exc}",
        ) from exc
