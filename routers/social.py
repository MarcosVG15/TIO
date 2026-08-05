"""Social feed, likes, follows, and people discovery.

camelCase on the wire, matching the chat endpoints this screen sits beside.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

import accounts
import social as social_service
from DATABASE.ORM import Account, PostVisibility
from deps import current_account

router = APIRouter(tags=["social"])


class PostCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    caption: Optional[str] = Field(default=None, max_length=4000)
    image_url: Optional[str] = Field(
        default=None, max_length=2048, validation_alias="imageUrl"
    )
    trip_id: Optional[str] = Field(default=None, validation_alias="tripId")
    location_id: Optional[str] = Field(default=None, validation_alias="locationId")
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    visibility: PostVisibility = PostVisibility.PUBLIC


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.get("/social/feed")
def feed(account: Account = Depends(current_account)) -> dict[str, Any]:
    """Posts from you, your friends, people you follow, plus anything public.

    Newest first. Wrapped in {"posts": [...]} - the screen reads
    `data.posts` and would throw on a bare array.
    """
    return {"posts": social_service.feed(account.account_id)}


@router.post("/social/posts", status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate, account: Account = Depends(current_account)
) -> dict[str, Any]:
    """Publish a post.

    Nothing in the frontend calls this yet - without it the feed can never
    have content, since there is no other way to create a Post.
    """
    try:
        return social_service.create_post(
            account_id=account.account_id,
            caption=payload.caption,
            image_url=payload.image_url,
            trip_id=payload.trip_id,
            location_id=payload.location_id,
            rating=payload.rating,
            visibility=payload.visibility,
        )
    except social_service.InvalidPost as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/social/posts/{post_id}/like")
def like_post(
    post_id: str, account: Account = Depends(current_account)
) -> dict[str, Any]:
    """Toggle. The UI sends the same POST to like and to unlike, so the
    server decides from current state and returns the new one.
    """
    try:
        return social_service.toggle_like(account.account_id, post_id)
    except social_service.PostNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="post not found"
        ) from exc


@router.get("/social/posts/{post_id}/comments")
def list_comments(
    post_id: str, account: Account = Depends(current_account)
) -> dict[str, Any]:
    """Comments oldest-first. `mine` marks the caller's own, which is what
    decides whether a delete control is shown.
    """
    try:
        return {"comments": social_service.list_comments(account.account_id, post_id)}
    except social_service.PostNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="post not found"
        ) from exc


@router.post(
    "/social/posts/{post_id}/comments", status_code=status.HTTP_201_CREATED
)
def add_comment(
    post_id: str,
    payload: CommentCreate,
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    try:
        return social_service.add_comment(
            account.account_id, post_id, payload.body
        )
    except social_service.PostNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="post not found"
        ) from exc
    except social_service.InvalidPost as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.delete(
    "/social/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_comment(
    comment_id: str, account: Account = Depends(current_account)
) -> None:
    """Only the author can delete. Someone else's comment and a missing one
    both return 404, so ids cannot be probed.
    """
    try:
        social_service.delete_comment(account.account_id, comment_id)
    except social_service.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="comment not found"
        ) from exc


@router.post("/users/{user_id}/follow")
def follow_user(
    user_id: str, account: Account = Depends(current_account)
) -> dict[str, Any]:
    """Connect with someone. Following IS friendship now.

    Pressing this sends a friend request, accepts a pending one from them, or
    disconnects if you are already connected. The path keeps its old name so
    the existing UI keeps working.
    """
    try:
        return social_service.toggle_connection(account.account_id, user_id)
    except social_service.NotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="account not found"
        ) from exc
    except social_service.InvalidPost as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/users/suggested")
def suggested_users(
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    friendsOnly: bool = False,
    account: Account = Depends(current_account),
) -> dict[str, Any]:
    """People worth connecting with, those heading to your destinations first.

    Each carries friendshipStatus so the UI can show Add friend / Requested /
    Accept / Friends without a request per person.

    friendsOnly=true returns only accepted friends - use it for the chat
    member picker, since the chat endpoints reject anyone else with a 403.
    """
    return {
        "users": accounts.suggested_people(
            account.account_id, friends_only=friendsOnly
        )
    }
