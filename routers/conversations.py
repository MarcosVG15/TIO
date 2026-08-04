"""Group chat.

Backed by the Conversation / Message / GroupMember tables, which exist -
these are unimplemented rather than undesigned.

Request bodies are deliberately not declared. A stub that validates would
return 422 on an unexpected shape, and the frontend renders 422 as a field
error instead of the "coming soon" message it shows for 501.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from DATABASE.ORM import Account
from deps import current_account, not_implemented

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
def list_conversations(account: Account = Depends(current_account)):
    """Every conversation the caller is a member of."""
    raise not_implemented("conversations")


@router.post("/conversations")
def create_conversation(account: Account = Depends(current_account)):
    """Start a conversation."""
    raise not_implemented("creating a conversation")


@router.get("/conversations/{chat_id}/messages")
def list_messages(chat_id: str, account: Account = Depends(current_account)):
    """Messages in a conversation, oldest first."""
    raise not_implemented("messages")


@router.post("/conversations/{chat_id}/messages")
def send_message(chat_id: str, account: Account = Depends(current_account)):
    """Send a message. Frontend sends {"body": "<text>"}."""
    raise not_implemented("sending messages")


@router.post("/conversations/{chat_id}/members")
def add_members(chat_id: str, account: Account = Depends(current_account)):
    """Add people to a conversation. Frontend sends {"memberIds": [...]}."""
    raise not_implemented("adding members")
