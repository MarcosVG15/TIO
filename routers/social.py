"""Social graph: feed, likes, follows, suggested people.

Nothing here has database tables yet - the ERD has no posts, likes or
follows. These are the largest gap between what the UI assumes and what the
schema supports.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from DATABASE.ORM import Account
from deps import current_account, not_implemented

router = APIRouter(tags=["social"])


@router.get("/social/feed")
def feed(account: Account = Depends(current_account)):
    """Posts from people the caller follows."""
    raise not_implemented("the social feed")


@router.post("/social/posts/{post_id}/like")
def like_post(post_id: str, account: Account = Depends(current_account)):
    raise not_implemented("liking posts")


@router.post("/users/{user_id}/follow")
def follow_user(user_id: str, account: Account = Depends(current_account)):
    raise not_implemented("following people")


@router.get("/users/suggested")
def suggested_users(
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    account: Account = Depends(current_account),
):
    """People to follow, optionally near a coordinate."""
    raise not_implemented("suggested people")
