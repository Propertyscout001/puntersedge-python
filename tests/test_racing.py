"""Book-vs-book racing, and the two ways it could quietly become a bet instead of an arb.

A race is an N-way market where N is the field, so the failure modes are sharper than for a
two-way h2h: back a subset of the field and you have not hedged anything, you have simply
backed those runners. Both tests for that shape are here, plus the one that keeps the
server's verdict authoritative.

Fixtures mirror the live /v1/racing/best-odds payload measured 2026-08-23.
"""
from __future__ import annotations

import pytest

from puntersedge.arb import ArbKind
from puntersedge.arb.gates import GateConfig, refusal_reasons
from puntersedge.arb.parse import from_racing_best_odds
from puntersedge.arb.sizing import size


def runner(name, price, book="sportsbet", age=10):
    return {
        "name": name, "number": 1, "barrier": 1,
        "best_win": {"price": price, "bookmaker": book,
                     "age_seconds": age, "source_url": "https://example/" + name},
    }


def race(runners, market_percentage, scratchings=(), category="horse"):
    return {
        "race_id": "r1", "venue": "Flemington", "race_number": "7",
        "race_name": "Test Plate", "category": category, "country": "AU",
        "start_time": "2026-08-23T07:24:00Z",
        "market_percentage": market_percentage,
        "runners": runners, "scratchings": list(scratchings),
    }


# ── the shape ────────────────────────────────────────────────────────────────────────

def test_one_leg_per_runner():
    [o] = from_racing_best_odds([race([runner("A", 3.0), runner("B", 4.0, "tab"),
                                       runner("C", 5.0, "neds")], 78.3)])
    assert o.kind is ArbKind.RACING_BACK_BACK
    assert [l.selection for l in o.legs] == ["A", "B", "C"]
    assert [l.book for l in o.legs] == ["sportsbet", "tab", "neds"]
    assert o.sport == "horse"          # the category rides in the sport slot
    assert "Flemington" in o.event_name and "R7" in o.event_name


def test_racing_prices_carry_their_own_age_so_no_enrichment_is_needed():
    """Sports needs a second paid call to learn how old a price is. Racing does not, and a
    scanner that enriched racing anyway would spend a credit on /v1/sports/horse/odds — not
    a sport_key, so a guaranteed 404."""
    [o] = from_racing_best_odds([race([runner("A", 3.0, age=12),
                                       runner("B", 4.0, "tab", age=30)], 58.3)])
    assert [l.quote_age_s for l in o.legs] == [12.0, 30.0]
    assert refusal_reasons(o, GateConfig()) == [] or "unknown_age" not in refusal_reasons(o, GateConfig())


# ── the server stays the authority ───────────────────────────────────────────────────

def test_is_arb_comes_from_market_percentage_not_local_arithmetic():
    """market_percentage is the server's 100*sum(1/best_win) over exactly the LIVE runners,
    scratchings already excluded. Recomputing locally is how the drawless-soccer phantom got
    in, and racing's version of it is backing a field that has a runner missing."""
    under = from_racing_best_odds([race([runner("A", 3.0), runner("B", 4.0)], 58.33)])[0]
    over = from_racing_best_odds([race([runner("A", 1.5), runner("B", 1.5)], 133.3)])[0]
    assert under.raw["is_arb"] is True
    assert over.raw["is_arb"] is False
    assert under.edge_pct == pytest.approx(41.67, abs=0.01)
    assert over.edge_pct == 0.0, "an over-round market must report 0, never a negative edge"


def test_a_race_with_no_server_verdict_is_dropped():
    assert from_racing_best_odds([race([runner("A", 3.0), runner("B", 4.0)], None)]) == []


def test_gate_still_refuses_when_the_server_declined():
    over = from_racing_best_odds([race([runner("A", 1.5, "tab"), runner("B", 1.5)], 133.3)])[0]
    assert "server_not_arb" in refusal_reasons(over, GateConfig())


# ── the field must be covered ────────────────────────────────────────────────────────

def test_a_runner_with_no_price_drops_the_whole_race():
    """THE racing failure mode. Backing 7 of 8 runners is not an arb, it is a bet that the
    eighth loses — and it would price as a huge edge because the missing runner contributes
    nothing to inv_sum."""
    r = race([runner("A", 3.0), runner("B", 4.0)], 58.3)
    r["runners"].append({"name": "C", "number": 3, "best_win": {}})
    assert from_racing_best_odds([r]) == []


def test_an_unbackable_price_drops_the_whole_race():
    r = race([runner("A", 3.0), runner("B", 1.0)], 58.3)      # 1.0 pays nothing
    assert from_racing_best_odds([r]) == []


def test_scratchings_are_never_backed():
    """A scratched runner must not become a leg. The payload keeps them in their own list
    and market_percentage already excludes them; this pins that the parser reads `runners`
    and nothing else."""
    r = race([runner("A", 3.0), runner("B", 4.0)], 58.3,
             scratchings=[{"name": "Withdrawn", "number": 9, "barrier": 9}])
    [o] = from_racing_best_odds([r])
    assert [l.selection for l in o.legs] == ["A", "B"]
    assert "Withdrawn" not in [l.selection for l in o.legs]


# ── it has to survive the rest of the pipeline ───────────────────────────────────────

def test_a_full_field_sizes():
    """15 runners is an ordinary field and must not hit the leg cap — the cap was 12 until
    racing arrived, so a 15-runner race raised instead of sizing."""
    [o] = from_racing_best_odds([race([runner("R%d" % i, 16.0, "book%d" % i)
                                       for i in range(15)], 93.75)])
    s = size(o, 200, step=1.0)
    assert s.viable, s.reason
    assert len(s.legs) == 15
    assert s.profit > 0
    assert s.total_staked <= 200 + 1e-9


def test_kind_can_be_switched_off_by_config():
    [o] = from_racing_best_odds([race([runner("A", 3.0), runner("B", 4.0, "tab")], 58.3)])
    sports_only = GateConfig(kinds={ArbKind.SPORTS_BACK_BACK})
    assert "wrong_kind" in refusal_reasons(o, sports_only)
    assert "wrong_kind" not in refusal_reasons(o, GateConfig())
