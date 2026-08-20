"""Data model for a book-vs-book arbitrage opportunity.

Deliberately smaller than the operator's internal engine model. Everything here is
information a customer of the public API can actually obtain and act on; nothing here
refers to exchange market ids, account state, or bet placement.

Ported from the PuntersEdge arb engine (`models.py`, `feed.py`) with the execution-side
fields removed. Where this model deviates from the engine's, the deviation is deliberate
and the reason is recorded on the field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ArbKind(str, Enum):
    """The arb shapes a customer of the public API can see.

    There is deliberately NO racing back/lay kind. That shape needs a Betfair exchange lay
    price, and both `/v1/arb/racing` and `/v1/racing/exchange` return HTTP 410 to every
    customer key — the exchange side is withheld pending a Betfair data licence. Adding the
    kind would let a caller build a scanner that can only ever return nothing, which is the
    failure mode this package exists to prevent. Use `racing_best_odds()` for cross-book
    racing value instead; it needs no exchange.
    """

    SPORTS_BACK_BACK = "sports_back_back"  # h2h, one back per outcome, best price each
    LINES_BACK_BACK = "lines_back_back"    # spreads/totals, two matched lines across books


UNKNOWN_AGE = None
"""Sentinel for `Leg.quote_age_s`: the feed did not tell us how old this price is.

This is NOT the same as an age of 0.0, and conflating the two is the single most likely way
to go silently blind on this data. The engine's `_fresh()` tests `if o.back.quote_age_s and
... > max`, so a falsy 0.0 passes the gate — correct there, because the racing feed it reads
always carries an age. `/v1/arb/sports` and `/v1/arb/lines` carry NO age field at all, so the
same code against this feed would pass every price forever while looking like it was
checking. See `gates.UnknownAge` for how that is handled here.
"""


@dataclass
class Leg:
    """One priced selection you would place a back bet on."""

    book: str
    selection: str
    odds: float
    # None means UNKNOWN, not fresh — see UNKNOWN_AGE above. `/v1/sports/{key}/odds` exposes
    # `data_age_seconds` / `freshest_age_seconds` / `stale` per event; the arb endpoints do
    # not, so a scanner has to enrich from the odds endpoint to populate this.
    quote_age_s: Optional[float] = None
    event_url: str = ""

    def implied_prob(self) -> float:
        """1/odds. Raises on non-positive odds rather than returning nonsense."""
        if self.odds <= 0:
            raise ValueError("odds must be > 0, got %r" % (self.odds,))
        return 1.0 / self.odds


@dataclass
class Opportunity:
    """A candidate arb: one leg per outcome, priced at the best book for that outcome.

    `legs` is a LIST, not the engine's fixed back/lay pair. The engine models every arb as
    two legs because the shape it executes is back-one-book / lay-Betfair. Book-vs-book is
    not that shape: soccer h2h settles home/draw/away and needs three legs. A two-leg model
    literally cannot represent a three-way market, and a drawless two-way reading of a
    three-way market is exactly the phantom that produced 13 of 25 bad sports alerts before
    2026-08-15 (Juventus 2.65 + Inter 2.55 reads as a 23% arb; the draw loses both legs).
    The server rejects that case via `covers_outcome_space()`; representing it faithfully
    here means the client cannot reintroduce it.
    """

    kind: ArbKind
    event_name: str
    sport: str
    legs: List[Leg]
    # The edge the FEED quoted, as a percentage. Kept separate from anything computed here:
    # `edge_ceiling` exists precisely to catch a quoted edge that the data cannot support,
    # so it must be gated on what was quoted, not on a locally recomputed number.
    edge_pct: float = 0.0
    source: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def books(self) -> List[str]:
        return [(leg.book or "").strip().lower() for leg in self.legs]

    def inv_sum(self) -> float:
        """Sum of implied probabilities. Below 1.0 is a locked margin."""
        return sum(leg.implied_prob() for leg in self.legs)


@dataclass(frozen=True)
class Verdict:
    """The result of running the gates over one opportunity.

    Carries ALL refusal reasons, never just the first. A refused opp normally trips several
    conditions at once, so a single first-match token makes any ratio an artefact of the
    order the conditions happen to be written in — this cost the operator a real
    misdiagnosis ("the feed rejected 484 of 488 arbs" read as a detection crisis when most
    of it was scope policy working correctly).
    """

    ok: bool
    reasons: List[str]
    verdict_class: str  # "clear" | "policy" | "data"

    @property
    def reason(self) -> str:
        """First reason, for one-line logging only. Never count with this."""
        return self.reasons[0] if self.reasons else ""

    def __bool__(self) -> bool:
        return self.ok
