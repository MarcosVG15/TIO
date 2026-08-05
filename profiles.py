"""Reading and updating a user's own profile.

Spans two tables: identity and preferences live on Account, travel profile
lives on Personality. Preferences are kept in Account.account_metadata rather
than as columns - they are UI toggles, not something the backend branches on,
and adding a column per toggle would mean a migration every time the settings
screen grows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select

from accounts import release_identifiers
from DATABASE.ORM import Account, Personality, session_scope

PREFERENCE_KEYS = ("push_notifications", "public_profile")


class AccountMissing(Exception):
    """No live account behind this session token."""


def _preferences(account: Account) -> dict[str, bool]:
    meta = account.account_metadata or {}
    return {key: bool(meta.get(key, True)) for key in PREFERENCE_KEYS}


def _account_out(account: Account) -> dict[str, Any]:
    return {
        "account_id": str(account.account_id),
        "email": account.email,
        "name": account.name,
        "surname": account.surname,
        "auth_provider": account.auth_provider.value,
        "bio": account.bio,
        "avatar_url": account.avatar_url
        or (account.account_metadata or {}).get("picture"),
    }


def _has_answered(personality: Optional[Personality]) -> bool:
    """Onboarding is complete once answers exist.

    Not "a Personality row exists" - resetting the questionnaire clears the
    answers but keeps the row, so History and its vec_snapshots survive.
    """
    if personality is None:
        return False
    return bool((personality.discussion_json or {}).get("questionnaire"))


def _payload(account: Account, personality: Optional[Personality]) -> dict[str, Any]:
    discussion = (personality.discussion_json or {}) if personality else {}
    return {
        "account": _account_out(account),
        "onboarding_completed": _has_answered(personality),
        "preferences": _preferences(account),
        "questionnaire": discussion.get("questionnaire") or [],
        "profile_summary": personality.profile_paragraph if personality else None,
        "dietary_restriction": (personality.dietary_restriction or {})
        if personality
        else {},
        "accessibility_needs": (personality.accessibility_needs or {})
        if personality
        else {},
        "preferred_language": list(personality.preferred_language or [])
        if personality
        else [],
        "embedding_status": personality.embedding_status.value if personality else None,
        "ready": bool(personality and personality.personality_vector is not None),
        "deactivated_at": account.deleted_at,
    }


def _load(session, account_id: UUID) -> tuple[Account, Optional[Personality]]:
    account = session.scalar(
        select(Account).where(Account.account_id == account_id)
    )
    if account is None:
        raise AccountMissing(str(account_id))
    personality = session.scalar(
        select(Personality).where(Personality.account_id == account_id)
    )
    return account, personality


def get_profile(account_id: UUID) -> dict[str, Any]:
    with session_scope() as session:
        account, personality = _load(session, account_id)
        return _payload(account, personality)


def update_profile(account_id: UUID, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update.

    `changes` holds only the keys the caller actually sent, so an absent key
    and an explicit null mean different things - the latter is how the
    frontend asks to wipe the questionnaire.
    """
    with session_scope() as session:
        account, personality = _load(session, account_id)

        if "name" in changes and changes["name"]:
            account.name = changes["name"].strip()
        if "surname" in changes:
            surname = changes["surname"]
            account.surname = surname.strip() if surname else None
        if "bio" in changes:
            bio = changes["bio"]
            account.bio = bio.strip() if bio else None
        if "avatar_url" in changes:
            avatar = changes["avatar_url"]
            account.avatar_url = avatar.strip() if avatar else None

        # Accept both the flat and nested forms the frontend uses.
        prefs = dict(changes.get("preferences") or {})
        for key in PREFERENCE_KEYS:
            if key in changes and changes[key] is not None:
                prefs[key] = changes[key]
        if prefs:
            # Reassign rather than mutate - SQLAlchemy does not track
            # in-place edits to a JSONB dict.
            merged = dict(account.account_metadata or {})
            for key in PREFERENCE_KEYS:
                if key in prefs:
                    merged[key] = bool(prefs[key])
            account.account_metadata = merged

        # Reset: clear the answers, keep the row so History survives.
        resetting = ("questionnaire" in changes and changes["questionnaire"] is None) or (
            changes.get("onboarding_completed") is False
        )
        if resetting and personality is not None:
            personality.discussion_json = {}
            personality.profile_paragraph = None
            personality.personality_vector = None

        deactivating = changes.get("is_active") is False
        original_email = account.email

        if deactivating:
            account.deleted_at = datetime.now(timezone.utc)
            if changes.get("deactivation_reason"):
                meta = dict(account.account_metadata or {})
                meta["deactivation_reason"] = changes["deactivation_reason"]
                account.account_metadata = meta
            # Email and provider subject are UNIQUE. Without releasing them
            # the person could never sign up again with their own address.
            release_identifiers(account)

        session.flush()
        payload = _payload(account, personality)
        if deactivating:
            # Report the address they actually used, not the placeholder.
            payload["account"]["email"] = original_email
        return payload
