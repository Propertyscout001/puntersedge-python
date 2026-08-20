"""Arbitrage tooling on top of the PuntersEdge API.

The API finds candidates. This package decides which candidates are real.

    from puntersedge import PuntersEdge
    from puntersedge.arb import GateConfig, evaluate, from_sports_payload

    client = PuntersEdge(api_key="...")
    cfg = GateConfig(bettable_books={"sportsbet", "tab", "neds"})

    for opp in from_sports_payload(client.arb_sports(sport_key="afl")):
        v = evaluate(opp, cfg)
        print(opp.event_name, v.ok, v.reasons)

WHAT THIS CANNOT DO
-------------------
Book-vs-book only. Racing back/lay — back at a bookmaker, lay on the Betfair exchange —
is not available to API customers: `/v1/arb/racing` and `/v1/racing/exchange` both return
HTTP 410, because the exchange side is withheld pending a Betfair data licence. There is no
flag to turn it on and no plan being waited for. For cross-book racing value that needs no
exchange, use `client.racing_best_odds()`.

NO PROFIT IS CLAIMED OR IMPLIED
-------------------------------
Book-vs-book arbitrage on Australian h2h markets is thin, intermittent, and self-limiting:
bookmakers restrict accounts that do it, usually within weeks. This package will not make
you money and is not sold on the basis that it will. It is instrumentation — it tells you
which of the feed's candidates survive scrutiny, and, just as importantly, why the rest do
not. Decide for yourself whether what is left is worth acting on.

Nothing here places a bet, holds a credential, or touches a bookmaker account.
"""
from __future__ import annotations

from .gates import (
    REASON_CLASS,
    GateConfig,
    UnknownAge,
    classify,
    evaluate,
    passes,
    refusal_reasons,
)
from .models import UNKNOWN_AGE, ArbKind, Leg, Opportunity, Verdict
from .parse import from_lines_payload, from_sports_payload
from .sizing import LegStake, Sizing, minimum_viable_total, size, theoretical_split

__all__ = [
    # models
    "ArbKind",
    "Leg",
    "Opportunity",
    "Verdict",
    "UNKNOWN_AGE",
    # sizing
    "size",
    "Sizing",
    "LegStake",
    "minimum_viable_total",
    "theoretical_split",
    # gates
    "GateConfig",
    "UnknownAge",
    "REASON_CLASS",
    "classify",
    "evaluate",
    "passes",
    "refusal_reasons",
    # parsing
    "from_sports_payload",
    "from_lines_payload",
]
