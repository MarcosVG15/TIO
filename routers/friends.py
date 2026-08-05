"""Friend requests.

camelCase on the wire, matching the chat and people endpoints that this
screen sits next to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

import friends
from DATABASE.ORM import Account
from deps import current_account

router = APIRouter(prefix="/friends", tags=["friends"])


class FriendRequestIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    account_id: str = Field(validation_alias="accountId")


@router.get("")
def list_friends(account: Account = Depends(current_account)):
    """Accepted friends only. These are the people addable to a group chat."""
    return {"friends": friends.list_friends(account.account_id)}


@router.get("/requests")
def list_requests(account: Account = Depends(current_account)):
    """Pending requests, split by direction so the UI can show an inbox and
    a "waiting on them" list separately.
    """
    return friends.list_requests(account.account_id)


@router.post("/requests", status_code=status.HTTP_201_CREATED)
def send_request(
    payload: FriendRequestIn,
    account: Account = Depends(current_account),
):
    """Send an invite.

    If they already sent you one, this accepts it instead of creating a
    second, opposite request - that is plainly what the person means.
    """
    try:
        return friends.send_request(account.account_id, payload.account_id)
    except friends.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        ) from exc
    except friends.FriendError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.post("/requests/{requester_id}/accept")
def accept_request(
    requester_id: str,
    account: Account = Depends(current_account),
):
    try:
        return friends.respond(account.account_id, requester_id, accept=True)
    except friends.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/requests/{requester_id}/decline")
def decline_request(
    requester_id: str,
    account: Account = Depends(current_account),
):
    try:
        return friends.respond(account.account_id, requester_id, accept=False)
    except friends.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.delete("/{other_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_friend(
    other_id: str,
    account: Account = Depends(current_account),
) -> None:
    """Unfriend, or withdraw a request you sent."""
    try:
        friends.remove_friend(account.account_id, other_id)
    except friends.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
