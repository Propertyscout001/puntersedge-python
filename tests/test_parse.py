"""Parsing the real `/v1/arb/*` response shapes.

The fixtures below are the shapes from the live OpenAPI examples, not invented ones.
"""
from __future__ import annotations

from puntersedge.arb import (
    ArbKind,
    GateConfig,
    UnknownAge,
    evaluate,
    from_lines_payload,
    from_sports_payload,
)

SPORTS_ROW = {
    "event_id": "event_123",
    "sport_key": "afl",
    "home_team": "Lions",
    "away_team": "Magpies",
    "commence_time": "2026-06-07T09:30:00Z",
    "is_arb": True,
    "arb_pct": 2.4,
    "max_overlay_pct": 5.1,
    "optimal_stakes": [],
    "selections": [
        {"name": "Lions", "best_price": 2.10, "best_bookmaker": "sportsbet",
         "avg_price": 1.95, "overlay_pct": 7.7, "all_prices": []},
        {"name": "Magpies", "best_price": 2.10, "best_bookmaker": "tab",
         "avg_price": 1.98, "overlay_pct": 6.1, "all_prices": []},
    ],
}

LINES_ROW = {
    "event_id": "event_123",
    "sport_key": "afl",
    "home_team": "Lions",
    "away_team": "Magpies",
    "commence_time": "2026-06-07T09:30:00Z",
    "market_type": "totals",
    "opportunities": [{
        "side_a": {"name": "Over", "point": 38.5, "price": 2.05, "bookmaker": "sportsbet"},
        "side_b": {"name": "Under", "point": 39.5, "price": 2.05, "bookmaker": "tab"},
        "is_arb": True, "is_middle": True, "arb_pct": 2.4, "optimal_stakes": [],
    }],
}


def test_sports_payload_parses():
    opps = from_sports_payload([SPORTS_ROW])
    assert len(opps) == 1
    o = opps[0]
    assert o.kind is ArbKind.SPORTS_BACK_BACK
    assert o.event_name == "Lions v Magpies"
    assert o.sport == "afl"
    assert o.edge_pct == 2.4
    assert o.books == ["sportsbet", "tab"]
    assert [leg.odds for leg in o.legs] == [2.10, 2.10]
    assert o.source == "/v1/arb/sports"


def test_sports_legs_have_unknown_age_without_enrichment():
    """The feed carries no age. This must surface, not silently pass."""
    o = from_sports_payload([SPORTS_ROW])[0]
    assert all(leg.quote_age_s is None for leg in o.legs)
    assert "unknown_age" in evaluate(o).reasons


def test_ages_enrich_legs():
    o = from_sports_payload([SPORTS_ROW], ages={"event_123": 12.0})[0]
    assert all(leg.quote_age_s == 12.0 for leg in o.legs)
    assert evaluate(o).ok


def test_envelope_forms_accepted():
    for envelope in ({"results": [SPORTS_ROW]}, {"data": [SPORTS_ROW]}, [SPORTS_ROW]):
        assert len(from_sports_payload(envelope)) == 1


def test_junk_payload_yields_nothing_rather_than_raising():
    for junk in (None, {}, "nope", [1, 2, 3], {"results": "no"}):
        assert from_sports_payload(junk) == []
        assert from_lines_payload(junk) == []


def test_single_selection_row_skipped():
    row = dict(SPORTS_ROW, selections=SPORTS_ROW["selections"][:1])
    assert from_sports_payload([row]) == []


def test_lines_payload_parses():
    opps = from_lines_payload([LINES_ROW])
    assert len(opps) == 1
    o = opps[0]
    assert o.kind is ArbKind.LINES_BACK_BACK
    assert o.books == ["sportsbet", "tab"]
    assert o.edge_pct == 2.4
    assert "totals" in o.event_name
    # the point is part of the selection label, so two lines are distinguishable
    assert o.legs[0].selection == "Over 38.5"
    assert o.legs[1].selection == "Under 39.5"


def test_lines_middle_flag_preserved_but_does_not_relax_gates():
    o = from_lines_payload([LINES_ROW], ages={"event_123": 5.0})[0]
    assert o.raw["is_middle"] is True
    stale = from_lines_payload([LINES_ROW], ages={"event_123": 99999.0})[0]
    assert "stale_quote" in evaluate(stale).reasons


def test_lines_row_missing_a_side_is_skipped():
    row = {**LINES_ROW, "opportunities": [{"side_a": LINES_ROW["opportunities"][0]["side_a"],
                                           "is_arb": True, "arb_pct": 2.4}]}
    assert from_lines_payload([row]) == []


def test_end_to_end_default_config_refuses_unenriched_feed():
    """Out of the box, an unenriched scan returns nothing and says exactly why.

    This is the intended first-run experience: loud, correct, and pointing at the fix.
    """
    opps = from_sports_payload([SPORTS_ROW])
    verdicts = [evaluate(o) for o in opps]
    assert not any(v.ok for v in verdicts)
    assert all(v.reasons == ["unknown_age"] for v in verdicts)
    # ...and opting in explicitly clears it
    cfg = GateConfig(unknown_age=UnknownAge.ALLOW)
    assert all(evaluate(o, cfg).ok for o in opps)
