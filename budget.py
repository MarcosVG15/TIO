"""What a plan costs, with every figure marked grounded or estimated.

The distinction this module exists to preserve: a flight fare came from a
provider and a museum ticket came from the database, but nobody quoted us a
price for lunch. Both belong in a total - a budget without food is not a budget
- but they must not look alike. A traveller who cannot tell which numbers were
quoted and which were guessed will either distrust all of them or, worse, trust
all of them.

So every line carries `grounded` and a `source`, the JSON keeps them, and the
totals are available split as well as combined. Nothing here invents a number
without saying so, and the LLM is never asked for a figure at all - it composes
the plan, and the arithmetic happens in Python where it can be checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

#: Per person per day, for food and local transport, by declared budget tier.
#: Deliberately coarse: these are the cost of eating and getting about in a
#: mid-range European city, and pretending to more precision than that would be
#: false. They are the reason every line built from them is marked estimated.
#: Override with BUDGET_DAILY_<TIER> if a destination makes them badly wrong.
_DAILY_BY_TIER: dict[str, Decimal] = {
    "shoestring": Decimal("35"),
    "budget": Decimal("45"),
    "moderate": Decimal("75"),
    "comfortable": Decimal("110"),
    "luxury": Decimal("200"),
}
_DEFAULT_DAILY = Decimal("75")

#: Per room per night, by tier, for when no hotel rate could be fetched. A
#: budget that silently omits accommodation understates a trip by roughly half,
#: which is worse than an estimate that says it is one.
_NIGHTLY_BY_TIER: dict[str, Decimal] = {
    "shoestring": Decimal("45"),
    "budget": Decimal("65"),
    "moderate": Decimal("110"),
    "comfortable": Decimal("175"),
    "luxury": Decimal("320"),
}
_DEFAULT_NIGHTLY = Decimal("110")

#: Two to a room. Coarse, but it is the difference between pricing a family of
#: four as one room and as four.
_PER_ROOM = 2


@dataclass(frozen=True)
class CostLine:
    """One row of a budget."""

    label: str
    amount: Decimal
    currency: str
    #: True when the figure came from a provider or the database, False when
    #: this module worked it out. Never omitted, never inferred by a caller.
    grounded: bool
    #: Where it came from, in words a traveller could read: "Travelpayouts
    #: cached fare", "estimated from budget tier: moderate".
    source: str
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "amount": float(self.amount),
            "currency": self.currency,
            "grounded": self.grounded,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass
class Budget:
    """The lines, and the arithmetic over them."""

    currency: str = "EUR"
    travellers: int = 1
    lines: list[CostLine] = field(default_factory=list)

    def add(
        self,
        label: str,
        amount: Optional[Decimal],
        *,
        grounded: bool,
        source: str,
        detail: Optional[str] = None,
    ) -> None:
        """Add a line, ignoring anything absent or non-positive.

        A zero line is noise and a negative one is a bug upstream; neither
        should reach a traveller as "Flights: 0.00".
        """
        if amount is None or amount <= 0:
            return
        self.lines.append(
            CostLine(
                label=label,
                amount=Decimal(amount).quantize(Decimal("0.01")),
                currency=self.currency,
                grounded=grounded,
                source=source,
                detail=detail,
            )
        )

    @property
    def grounded_total(self) -> Decimal:
        return sum(
            (line.amount for line in self.lines if line.grounded), Decimal("0")
        ).quantize(Decimal("0.01"))

    @property
    def estimated_total(self) -> Decimal:
        return sum(
            (line.amount for line in self.lines if not line.grounded), Decimal("0")
        ).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        return (self.grounded_total + self.estimated_total).quantize(Decimal("0.01"))

    @property
    def per_person(self) -> Decimal:
        return (self.total / max(1, self.travellers)).quantize(Decimal("0.01"))

    @property
    def is_complete(self) -> bool:
        """Whether anything was actually priced.

        An all-estimated budget is a guess with a total attached, and the UI
        should be able to say so rather than presenting it as a costing.
        """
        return any(line.grounded for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "travellers": self.travellers,
            "lines": [line.to_dict() for line in self.lines],
            "grounded_total": float(self.grounded_total),
            "estimated_total": float(self.estimated_total),
            "total": float(self.total),
            "per_person": float(self.per_person),
            "has_quoted_prices": self.is_complete,
            # Said once, here, so every surface that renders a budget carries
            # it and none of them has to remember to.
            "disclaimer": (
                "Flight and hotel prices are indicative cached rates, not "
                "quotes. Food and local transport are estimates. Confirm every "
                "price on the booking page."
            ),
        }


def daily_allowance(budget_tier: Optional[str]) -> Decimal:
    """Food and local transport, per person per day, from the declared tier."""
    if not budget_tier:
        return _DEFAULT_DAILY
    return _DAILY_BY_TIER.get(budget_tier.strip().lower(), _DEFAULT_DAILY)


def accommodation_estimate(
    budget: Budget,
    nights: int,
    budget_tier: Optional[str],
    city: Optional[str] = None,
) -> None:
    """Add an estimated accommodation line, for when no rate was available.

    Only ever called as a fallback. A real cached rate is a grounded line with
    a booking link; this is a placeholder so the total is not quietly missing
    the second-largest cost of the trip, and it is marked accordingly.
    """
    if nights <= 0:
        return
    nightly = _NIGHTLY_BY_TIER.get(
        (budget_tier or "").strip().lower(), _DEFAULT_NIGHTLY
    )
    rooms = max(1, -(-max(1, budget.travellers) // _PER_ROOM))  # ceil division
    tier = (budget_tier or "moderate").lower()
    label = f"Accommodation - {city}" if city else "Accommodation"
    budget.add(
        label,
        nightly * nights * rooms,
        grounded=False,
        source=f"estimated from budget tier: {tier}",
        detail=(
            f"{nightly} {budget.currency} per room per night x {nights} "
            f"night(s) x {rooms} room(s) - no live rate was available"
        ),
    )


def living_costs(
    budget: Budget,
    days: int,
    budget_tier: Optional[str],
) -> None:
    """Add the estimated food and local-transport line.

    Kept separate from the grounded lines so a caller can build a
    quoted-prices-only budget simply by not calling it.
    """
    if days <= 0:
        return
    daily = daily_allowance(budget_tier)
    tier = (budget_tier or "moderate").lower()
    budget.add(
        "Food and local transport",
        daily * days * max(1, budget.travellers),
        grounded=False,
        source=f"estimated from budget tier: {tier}",
        detail=(
            f"{daily} {budget.currency} per person per day x {days} day(s) "
            f"x {budget.travellers} traveller(s)"
        ),
    )
