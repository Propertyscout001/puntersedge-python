"""Gate behaviour, including the phantom classes each gate was added to stop.

Several tests here are NEGATIVE tests: they assert that a known-bad opportunity is refused.
Those are the ones worth keeping — a gate that silently stops firing looks identical to a
gate that has nothing to refuse, and that is how the operator's monitors went blind before.
"""
from __future__ import annotations

import pytest

from puntersedge.arb import (
    ArbKind,
    GateConfig,
    Leg,
    Opportunity,
    UnknownAge,
    classify,
    evaluate,
    refusal_reasons,
)


def opp(legs, edge_pct=2.0, kind=ArbKind.SPORTS_BACK_BACK, is_arb=True, **raw):
    """A well-formed opportunity. Tests break ONE thing at a time from here."""
    payload = {"is_arb": is_arb}
    payload.update(raw)
    return Opportunity(
        kind=kind,
        event_name="Lions v Magpies",
        sport="afl",
        legs=legs,
        edge_pct=edge_pct,
        raw=payload,
    )


def legs(*pairs, age=10.0):
    return [Leg(book=b, selection=s, odds=o, quote_age_s=age) for b, s, o in pairs]


GOOD = [("sportsbet", "Lions", 2.10), ("tab", "Magpies", 2.10)]


# ── the happy path ───────────────────────────────────────────────────────────────────

def test_clean_arb_passes():
    v = evaluate(opp(legs(*GOOD)))
    assert v.ok, v.reasons
    assert v.verdict_class == "clear"
    assert bool(v) is True


# ── crosses test ─────────────────────────────────────────────────────────────────────

def test_overround_prices_are_not_an_arb():
    # 1/1.90 + 1/1.90 = 1.052 — a normal market with the book's margin in it. The feed
    # returns these; calling them arbs is the single most likely beginner error.
    v = evaluate(opp(legs(("sportsbet", "Lions", 1.90), ("tab", "Magpies", 1.90))))
    assert not v.ok
    assert "no_cross" in v.reasons


def test_exactly_even_is_not_an_arb():
    # inv_sum == 1.0 exactly: no margin. Must refuse, not round into acceptance.
    v = evaluate(opp(legs(("sportsbet", "Lions", 2.0), ("tab", "Magpies", 2.0))))
    assert "no_cross" in v.reasons


# ── the server's verdict is load-bearing ─────────────────────────────────────────────

def test_drawless_soccer_phantom_is_refused():
    """The 2026-08-15 phantom: a three-way market read as two-way.

    Juventus 2.65 + Inter 2.55 sums to 0.77 — arithmetically a 23% arb — but the draw is
    uncovered and loses BOTH legs. The client cannot detect this (it needs `max_outcomes`,
    which is not in the payload), so it must respect the server saying is_arb=False.
    """
    phantom = opp(
        legs(("sportsbet", "Juventus", 2.65), ("tab", "Inter", 2.55)),
        edge_pct=0.0,
        is_arb=False,
    )
    v = evaluate(phantom)
    assert not v.ok
    assert "server_not_arb" in v.reasons
    # The arithmetic alone would have accepted it — that is the point of this test.
    assert phantom.inv_sum() < 1.0
    assert "no_cross" not in v.reasons


def test_missing_is_arb_is_refused_not_assumed():
    o = opp(legs(*GOOD))
    o.raw.pop("is_arb")
    assert "server_not_arb" in refusal_reasons(o)


# ── single venue ─────────────────────────────────────────────────────────────────────

def test_same_book_both_legs_refused():
    v = evaluate(opp(legs(("sportsbet", "Lions", 2.10), ("sportsbet", "Magpies", 2.10))))
    assert "single_venue" in v.reasons


def test_book_name_casing_and_padding_do_not_defeat_single_venue():
    v = evaluate(opp(legs(("SportsBet", "Lions", 2.10), (" sportsbet ", "Magpies", 2.10))))
    assert "single_venue" in v.reasons


# ── odds sanity ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [1.0, 0.0, -2.0])
def test_non_positive_odds_refused(bad):
    v = evaluate(opp(legs(("sportsbet", "Lions", bad), ("tab", "Magpies", 2.10))))
    assert "bad_odds" in v.reasons
    # and the reciprocal-taking gate must not have been reached
    assert "no_cross" not in v.reasons


def test_empty_legs_refused():
    assert "bad_odds" in refusal_reasons(opp([]))


# ── edge ceiling ─────────────────────────────────────────────────────────────────────

def test_implausible_edge_refused():
    v = evaluate(opp(legs(("sportsbet", "Lions", 3.0), ("tab", "Magpies", 3.0)), edge_pct=33.0))
    assert "edge_ceiling" in v.reasons


def test_edge_ceiling_disabled_by_zero():
    cfg = GateConfig(max_edge_pct=0.0)
    v = evaluate(opp(legs(("sportsbet", "Lions", 3.0), ("tab", "Magpies", 3.0)), edge_pct=33.0), cfg)
    assert "edge_ceiling" not in v.reasons


def test_min_edge_is_policy_not_data():
    cfg = GateConfig(min_edge_pct=5.0)
    v = evaluate(opp(legs(*GOOD), edge_pct=1.0), cfg)
    assert "below_min_edge" in v.reasons
    assert v.verdict_class == "policy"


# ── freshness: the gate most likely to go silently blind ─────────────────────────────

def test_unknown_age_refused_by_default():
    """`/v1/arb/sports` carries no age field, so this is the DEFAULT state of the feed."""
    v = evaluate(opp(legs(*GOOD, age=None)))
    assert not v.ok
    assert "unknown_age" in v.reasons
    assert v.verdict_class == "data"


def test_unknown_age_can_be_allowed_explicitly():
    cfg = GateConfig(unknown_age=UnknownAge.ALLOW)
    v = evaluate(opp(legs(*GOOD, age=None)), cfg)
    assert v.ok, v.reasons


def test_zero_age_is_fresh_not_unknown():
    """The distinction the engine's falsy check cannot make. 0.0 is a real, fresh age."""
    v = evaluate(opp(legs(*GOOD, age=0.0)))
    assert v.ok, v.reasons
    assert "unknown_age" not in v.reasons


def test_stale_quote_refused():
    # Beyond the 900s default (one upstream poll cycle). 600s was used here until 0.2.1,
    # when the default moved from 120s — a threshold that rejected 100% of live sports data.
    v = evaluate(opp(legs(*GOOD, age=1200.0)))
    assert "stale_quote" in v.reasons
    assert "unknown_age" not in v.reasons  # distinct problems, distinct tokens


def test_a_price_at_the_measured_production_median_is_not_stale():
    """572s is the median observed age on the live sports feed. If the default refuses
    that, the gate rejects everything and reads as a quiet market."""
    assert evaluate(opp(legs(*GOOD, age=572.0))).ok


def test_one_stale_leg_is_enough():
    ls = [
        Leg("sportsbet", "Lions", 2.10, quote_age_s=5.0),
        Leg("tab", "Magpies", 2.10, quote_age_s=9999.0),
    ]
    assert "stale_quote" in refusal_reasons(opp(ls))


# ── bettable books ───────────────────────────────────────────────────────────────────

def test_book_you_have_no_account_with_is_policy():
    cfg = GateConfig(bettable_books={"sportsbet"})
    v = evaluate(opp(legs(*GOOD)), cfg)
    assert "book_not_bettable" in v.reasons
    assert v.verdict_class == "policy"


def test_no_bettable_set_means_no_opinion():
    assert evaluate(opp(legs(*GOOD)), GateConfig(bettable_books=None)).ok


def test_bettable_books_refuses_a_mapping():
    """The drift attractor: Set[str] -> Dict[str, credentials] is a one-token edit.

    Verified 2026-08-20 that BEFORE this guard, passing {"sportsbet": {"password": ...}}
    produced zero test failures and zero refusals — the gate does
    `set(opp.books).issubset(cfg.bettable_books)` and issubset compares against dict keys,
    so a credential store would have slid in under a green suite.
    """
    with pytest.raises(TypeError, match="never stores bookmaker logins"):
        GateConfig(bettable_books={"sportsbet": {"username": "u", "password": "p"}})


def test_bettable_books_still_accepts_a_plain_set():
    cfg = GateConfig(bettable_books={"sportsbet", "tab"})
    assert evaluate(opp(legs(*GOOD)), cfg).ok


# ── kinds ────────────────────────────────────────────────────────────────────────────

def test_kind_outside_config_is_policy():
    cfg = GateConfig(kinds={ArbKind.SPORTS_BACK_BACK})
    v = evaluate(opp(legs(*GOOD), kind=ArbKind.LINES_BACK_BACK), cfg)
    assert "wrong_kind" in v.reasons
    assert v.verdict_class == "policy"


# ── totality: one deformed opp must not kill the batch ───────────────────────────────

def test_deformed_opportunity_is_refused_not_raised():
    class Exploding:
        kind = ArbKind.SPORTS_BACK_BACK
        raw = {"is_arb": True}
        legs = property(lambda self: (_ for _ in ()).throw(AttributeError("boom")))
        books = property(lambda self: (_ for _ in ()).throw(AttributeError("boom")))

        def inv_sum(self):
            raise AttributeError("boom")

    rs = refusal_reasons(Exploding())  # must not raise
    assert "malformed" in rs
    assert rs.count("malformed") == 1  # recorded once, not once per gate


def test_a_bad_opp_does_not_stop_the_good_ones():
    batch = [opp(legs(*GOOD)), object(), opp(legs(*GOOD))]
    verdicts = [evaluate(o) if isinstance(o, Opportunity) else evaluate_safe(o) for o in batch]
    assert verdicts[0].ok and verdicts[2].ok


def evaluate_safe(o):
    return evaluate(o)  # relies on totality; asserts nothing raises


# ── classification ───────────────────────────────────────────────────────────────────

def test_any_policy_reason_makes_the_whole_refusal_policy():
    # Mixed: a data defect AND a scope decision. Counting this as a data defect would
    # inflate the defect number with an opp we never wanted.
    assert classify(["stale_quote", "book_not_bettable"]) == "policy"


def test_pure_data_refusal_is_data():
    assert classify(["stale_quote", "no_cross"]) == "data"


def test_unregistered_token_counts_as_data():
    """A reason someone forgot to register must surface, not be absorbed into policy."""
    assert classify(["some_new_reason"]) == "data"


def test_no_reasons_is_clear():
    assert classify([]) == "clear"


# ── reasons are complete, not first-match ────────────────────────────────────────────

def test_all_reasons_returned_not_just_the_first():
    cfg = GateConfig(bettable_books={"nowhere"})
    rs = refusal_reasons(opp(legs(("sportsbet", "L", 1.9), ("sportsbet", "M", 1.9), age=None)), cfg)
    for expected in ("no_cross", "single_venue", "unknown_age", "book_not_bettable"):
        assert expected in rs, (expected, rs)


def test_reason_property_is_first_of_reasons():
    v = evaluate(opp(legs(("sportsbet", "L", 1.9), ("sportsbet", "M", 1.9))))
    assert v.reason == v.reasons[0]
