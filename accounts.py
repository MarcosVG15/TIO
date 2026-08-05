"""The only place Account rows are read or written."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select

import passwords
from auth import GoogleClaims
from DATABASE.ORM import (
    Account,
    AuthProvider,
    Friendship,
    FriendshipStatus,
    ItineraryItem,
    Location,
    Trip,
    session_scope,
)


class AccountConflict(Exception):
    """Raised when an email already belongs to a different identity."""


class InvalidCredentials(Exception):
    """Wrong password, or an account whose credential lives with a provider."""


class UnknownAccount(InvalidCredentials):
    """No account with that email. A subclass so callers that only care about
    "sign-in failed" can still catch InvalidCredentials."""


def _display_name(claims: GoogleClaims) -> str:
    return claims.given_name or claims.name or claims.email.split("@")[0]


def release_identifiers(account: Account) -> None:
    """Free a deactivated account's email and provider subject for reuse.

    Both columns are UNIQUE, so leaving the real values in place would block
    the person from ever signing up again with their own address. Rewriting
    them to per-account placeholders keeps the row - trips, History and their
    vec_snapshots all survive - while making it unreachable.

    The originals are kept in metadata for support and audit.

    This is what makes deactivation final: a later sign-up creates a genuinely
    new account rather than reopening this one. Reactivating by signing in is
    deliberately not possible, because the alternative - letting anyone who
    knows a deactivated address re-register and inherit that account's data -
    is an account-takeover hole.
    """
    meta = dict(account.account_metadata or {})
    meta.setdefault("deactivated_email", account.email)
    if account.user_id:
        meta.setdefault("deactivated_user_id", account.user_id)
    account.account_metadata = meta

    marker = str(account.account_id)
    account.email = f"deleted+{marker}@deleted.invalid"
    if account.user_id:
        # Cannot be NULL for provider accounts - the check constraint
        # requires it - so replace rather than clear.
        account.user_id = f"deleted:{marker}"


def get_or_create_from_google(claims: GoogleClaims) -> Account:
    """Match on the Google subject id, never on email.

    `sub` is permanent; an email address can be renamed, or reassigned to a
    different person entirely in Google Workspace.
    """
    with session_scope() as session:
        # Live accounts only. A deactivated row must not be returned - it
        # would yield a session token that every later request rejects,
        # because current_account resolves live accounts only.
        account = session.scalar(
            select(Account).where(
                Account.user_id == claims.sub,
                Account.deleted_at.is_(None),
            )
        )

        if account is not None:
            account.email = claims.email
            account.name = _display_name(claims)
            account.surname = claims.family_name
            account.email_verified = claims.email_verified
            return account

        # A *live* account on this address is a real conflict.
        collision = session.scalar(
            select(Account).where(
                Account.email == claims.email,
                Account.deleted_at.is_(None),
            )
        )
        if collision is not None:
            raise AccountConflict(
                f"{claims.email} is already registered via "
                f"{collision.auth_provider.value}"
            )

        # Deactivated rows may still be holding this address or subject -
        # either from before identifiers were released on deactivation, or
        # from a row deactivated by another route. Both columns are UNIQUE,
        # so the insert below would fail on them. Free them first.
        stale = session.scalars(
            select(Account).where(
                Account.deleted_at.is_not(None),
                or_(
                    Account.email == claims.email,
                    Account.user_id == claims.sub,
                ),
            )
        ).all()
        for row in stale:
            release_identifiers(row)
        if stale:
            session.flush()

        account = Account(
            user_id=claims.sub,
            email=claims.email,
            name=_display_name(claims),
            surname=claims.family_name,
            email_verified=claims.email_verified,
            auth_provider=AuthProvider.GOOGLE,
            account_metadata={"picture": claims.picture} if claims.picture else {},
        )
        session.add(account)
        session.flush()
        return account


def create_with_password(
    email: str,
    password: str,
    name: str,
    surname: Optional[str] = None,
) -> Account:
    """Register a local account. email_verified stays False - nothing has
    proven the address belongs to whoever typed it.
    """
    email = email.strip().lower()

    with session_scope() as session:
        existing = session.scalar(select(Account).where(Account.email == email))
        if existing is not None:
            if existing.deleted_at is None:
                raise AccountConflict(
                    f"{email} is already registered via "
                    f"{existing.auth_provider.value}"
                )
            # Deactivated. Free the address and let the sign-up proceed as a
            # brand new account - the old row keeps its data but is detached
            # from this person's identity.
            release_identifiers(existing)
            session.flush()

        account = Account(
            user_id=None,
            password_hash=passwords.hash_password(password),
            email=email,
            name=name.strip(),
            surname=surname.strip() if surname else None,
            email_verified=False,
            auth_provider=AuthProvider.EMAIL,
        )
        session.add(account)
        session.flush()
        return account


def authenticate(email: str, password: str) -> Account:
    """Check an email/password pair. Raises InvalidCredentials on any failure."""
    email = email.strip().lower()

    with session_scope() as session:
        # Live accounts only. Authenticating a deactivated row would hand back
        # a token that current_account then rejects on every request.
        account = session.scalar(
            select(Account).where(
                Account.email == email,
                Account.deleted_at.is_(None),
            )
        )

        if account is None:
            # Still spend the CPU a real verification would, so response time
            # cannot be used to probe which addresses exist.
            passwords.burn_time()
            # The message does distinguish "no account" from "wrong password".
            # That is a deliberate trade: sign-up already reveals existence by
            # returning 409 on a duplicate email, so being coy here would buy
            # nothing while leaving a real user stuck on a sign-in form with
            # no idea they never registered.
            raise UnknownAccount(
                "No account found with that email. Sign up to create one."
            )

        if account.password_hash is None:
            # A provider account - the credential lives with Google, not here.
            passwords.burn_time()
            raise InvalidCredentials(
                f"This account uses {account.auth_provider.value.title()} "
                f"sign-in. Use the {account.auth_provider.value.title()} "
                f"button above."
            )

        if not passwords.verify_password(account.password_hash, password):
            raise InvalidCredentials("invalid email or password")

        # Argon2 parameters get stronger over time; upgrade opportunistically
        # while we have the plaintext in hand.
        if passwords.needs_rehash(account.password_hash):
            account.password_hash = passwords.hash_password(password)

        # No revive here. Deactivation releases the email (see
        # release_identifiers), so a deactivated account can never be found by
        # this lookup in the first place - reaching this point means the
        # account is live.
        return account


def _person(
    account: Account,
    friendship: Optional[str] = None,
    shared: Optional[list[str]] = None,
) -> dict[str, Any]:
    """The public view of someone else - never their email address.

    friendshipStatus lets the UI pick between "Add friend", "Requested",
    "Accept" and "Friends" without a second round trip per person.

    sharedInterests is always a list, never null: the people card calls
    .join() on it unconditionally and would crash on undefined.
    """
    shared = shared or []
    return {
        "id": str(account.account_id),
        "name": account.name,
        "handle": (account.email or "").split("@")[0],
        # Explicit avatar wins; otherwise fall back to the provider picture,
        # so a Google user has one without ever visiting settings.
        "avatarUrl": account.avatar_url
        or (account.account_metadata or {}).get("picture"),
        "bio": account.bio,
        "friendshipStatus": friendship,
        #: Names of destinations you both have planned.
        "sharedInterests": shared,
        "sharedDestinations": len(shared),
        #: No geo data on accounts, so distance is genuinely unknown rather
        #: than zero. The card renders it as "— km away".
        "distanceKm": None,
    }


def _friendship_map(session, account_id: UUID) -> dict[UUID, str]:
    """Everyone this account has any friendship row with, and its state.

    Queried here rather than through friends.py to avoid an import cycle -
    friends.py already depends on _person.
    """
    rows = session.execute(
        select(
            Friendship.requester_id,
            Friendship.addressee_id,
            Friendship.status,
        ).where(
            or_(
                Friendship.requester_id == account_id,
                Friendship.addressee_id == account_id,
            )
        )
    ).all()

    result: dict[UUID, str] = {}
    for requester, addressee, status in rows:
        other = addressee if requester == account_id else requester
        if status == FriendshipStatus.ACCEPTED:
            result[other] = "friends"
        elif status == FriendshipStatus.PENDING:
            # Direction matters: one shows "Requested", the other "Accept".
            result[other] = "pending_out" if requester == account_id else "pending_in"
        elif status == FriendshipStatus.BLOCKED:
            result[other] = "blocked"
    return result


def _shared_destinations(session, account_id: UUID) -> dict[UUID, list[str]]:
    """Which of my destinations each other traveller also has planned.

    A trip's destinations are its itinerary items' locations - Trip itself
    only records an origin, so there is nowhere else to read this from.
    Returns {} when I have no itinerary yet, which is the common case until
    trip planning is built.
    """
    mine = select(ItineraryItem.location_id).join(
        Trip, Trip.trip_id == ItineraryItem.trip_id
    ).where(
        Trip.owner_account_id == account_id,
        Trip.deleted_at.is_(None),
        ItineraryItem.location_id.is_not(None),
    )
    my_locations = set(session.scalars(mine).all())
    if not my_locations:
        return {}

    rows = session.execute(
        select(Trip.owner_account_id, Location.name)
        .join(ItineraryItem, ItineraryItem.trip_id == Trip.trip_id)
        .join(Location, Location.location_id == ItineraryItem.location_id)
        .where(
            ItineraryItem.location_id.in_(my_locations),
            Trip.owner_account_id != account_id,
            Trip.deleted_at.is_(None),
        )
        .distinct()
    ).all()

    out: dict[UUID, list[str]] = {}
    for owner, name in rows:
        out.setdefault(owner, []).append(name)
    return out


def _discoverable(account_id: UUID):
    """People who can be found: live, not you, and not opted out.

    public_profile defaults to true when the key is absent, so accounts that
    predate the preference are still discoverable.
    """
    return (
        select(Account)
        .where(
            Account.deleted_at.is_(None),
            Account.account_id != account_id,
            or_(
                Account.account_metadata["public_profile"].astext != "false",
                Account.account_metadata["public_profile"].is_(None),
            ),
        )
    )


def suggested_people(
    account_id: UUID, limit: int = 20, friends_only: bool = False
) -> list[dict]:
    """People worth connecting with.

    People heading to the same places come first, ranked by how many
    destinations you share, then everyone else newest-first so the list is
    never empty.

    Two opposite audiences, hence friends_only:
      - discovery (default) hides existing friends, since there is nothing
        to do with them there, but keeps pending requests so you can see an
        invite already in flight
      - friends_only=True returns *only* friends, which is what a "pick
        people for this chat" list needs - the chat endpoints reject anyone
        else with a 403
    """
    with session_scope() as session:
        friendships = _friendship_map(session, account_id)
        shared = _shared_destinations(session, account_id)

        candidates = session.scalars(
            _discoverable(account_id).order_by(Account.created_at.desc())
        ).all()

        if friends_only:
            people = [
                a for a in candidates if friendships.get(a.account_id) == "friends"
            ]
        else:
            people = [
                a
                for a in candidates
                if friendships.get(a.account_id) not in ("friends", "blocked")
            ]
        # Most shared destinations first; newest account breaks the tie,
        # which the query above already ordered by.
        people.sort(key=lambda a: len(shared.get(a.account_id, [])), reverse=True)

        return [
            _person(a, friendships.get(a.account_id), shared.get(a.account_id))
            for a in people[:limit]
        ]


def search_people(
    account_id: UUID, query: str, limit: int = 20, friends_only: bool = False
) -> list[dict]:
    """Find people by name or email. Matching on email is intentional - it is
    how you invite someone you know - but the address is never returned.

    friends_only narrows to accepted friends, for the chat member picker.
    """
    term = (query or "").strip()
    if len(term) < 2:
        return []

    pattern = f"%{term.lower()}%"
    with session_scope() as session:
        rows = session.scalars(
            _discoverable(account_id)
            .where(
                or_(
                    func.lower(Account.name).like(pattern),
                    func.lower(Account.email).like(pattern),
                )
            )
            .order_by(Account.name)
            .limit(limit)
        ).all()
        friendships = _friendship_map(session, account_id)
        shared = _shared_destinations(session, account_id)
        if friends_only:
            rows = [a for a in rows if friendships.get(a.account_id) == "friends"]
        return [
            _person(a, friendships.get(a.account_id), shared.get(a.account_id))
            for a in rows
        ]


def get_by_id(account_id: UUID) -> Optional[Account]:
    with session_scope() as session:
        return session.scalar(
            select(Account).where(
                Account.account_id == account_id,
                Account.deleted_at.is_(None),
            )
        )
