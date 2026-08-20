"""Scanner behaviour: credit discipline, the enrichment join, and anti-blindness.

The tests that matter most here are the ones asserting a poll CANNOT quietly succeed at
nothing — an unenriched sport must not pass as fresh, a budget must refuse before spending,
and "0 arbs" must always come with a reason.
"""
from __future__ import annotations

import pytest

from puntersedge.arb import (
    CreditBudgetExceeded,
    GateConfig,
    Scanner,
    UnknownAge,
)

AFL_ARB = {
    "event_id": "e1", "sport_key": "afl", "home_team": "Lions", "away_team": "Magpies",
    "is_arb": True, "arb_pct": 2.4,
    "selections": [
        {"name": "Lions", "best_price": 2.10, "best_bookmaker": "sportsbet"},
        {"name": "Magpies", "best_price": 2.10, "best_bookmaker": "tab"},
    ],
}
AFL_DUD = {
    "event_id": "e2", "sport_key": "afl", "home_team": "Blues", "away_team": "Saints",
    "is_arb": False, "arb_pct": 0.0,
    "selections": [
        {"name": "Blues", "best_price": 1.90, "best_bookmaker": "sportsbet"},
        {"name": "Saints", "best_price": 1.90, "best_bookmaker": "tab"},
    ],
}
NRL_ARB = {
    "event_id": "e3", "sport_key": "nrl", "home_team": "Storm", "away_team": "Broncos",
    "is_arb": True, "arb_pct": 1.8,
    "selections": [
        {"name": "Storm", "best_price": 2.15, "best_bookmaker": "neds"},
        {"name": "Broncos", "best_price": 2.05, "best_bookmaker": "tab"},
    ],
}


class Stub:
    def __init__(self, arbs=None, odds=None, odds_error=None):
        self.arbs = arbs if arbs is not None else [AFL_ARB, AFL_DUD, NRL_ARB]
        self._odds = odds or {}
        self.odds_error = odds_error or {}
        self.calls = []

    def arb_sports(self, sport_key=None):
        self.calls.append(("arb_sports", sport_key))
        return [r for r in self.arbs if sport_key in (None, r["sport_key"])]

    def arb_lines(self, sport_key=None):
        self.calls.append(("arb_lines", sport_key))
        return []

    def odds(self, sport, markets=None):
        self.calls.append(("odds", sport))
        if sport in self.odds_error:
            raise RuntimeError(self.odds_error[sport])
        return self._odds.get(sport, [])


FRESH = {
    "afl": [{"id": "e1", "bookmakers": [
        {"key": "sportsbet", "age_seconds": 12}, {"key": "tab", "age_seconds": 30}]}],
    "nrl": [{"id": "e3", "bookmakers": [
        {"key": "neds", "age_seconds": 20}, {"key": "tab", "age_seconds": 25}]}],
}


# ── the happy path ───────────────────────────────────────────────────────────────────

def test_poll_finds_the_real_arbs_and_ages_them():
    r = Scanner(Stub(odds=FRESH), GateConfig()).poll()
    assert r.candidates == 3
    assert len(r.arbs) == 2
    assert all(l.quote_age_s is not None for o in r.arbs for l in o.legs)
    assert r.diagnosis() is None


# ── credits ──────────────────────────────────────────────────────────────────────────

def test_free_gates_run_before_any_paid_enrichment():
    """The dud dies for free; only sports with survivors are paid for."""
    c = Stub(arbs=[AFL_DUD], odds=FRESH)
    r = Scanner(c, GateConfig()).poll()
    assert [k for k, _ in c.calls] == ["arb_sports"], "paid to enrich a sport with no survivors"
    assert r.credits_spent == 3
    assert r.reasons["server_not_arb"] == 1


def test_only_sports_with_survivors_are_enriched():
    c = Stub(odds=FRESH)
    Scanner(c, GateConfig()).poll()
    enriched = sorted(s for k, s in c.calls if k == "odds")
    assert enriched == ["afl", "nrl"]


def test_credit_cost_is_arb_plus_one_per_enriched_sport():
    r = Scanner(Stub(odds=FRESH), GateConfig()).poll()
    assert r.credits_spent == 3 + 2


def test_budget_refuses_before_spending_anything():
    c = Stub(odds=FRESH)
    with pytest.raises(CreditBudgetExceeded, match="Nothing was requested"):
        Scanner(c, GateConfig(), credit_budget=2).poll()
    assert c.calls == [], "made a request after claiming it would not"


def test_budget_reached_mid_enrichment_does_not_pass_unaged_legs():
    """The tempting-but-wrong behaviour is to let the un-enriched ones through."""
    c = Stub(odds=FRESH)
    r = Scanner(c, GateConfig(), credit_budget=4).poll()   # 3 for arb, room for ONE odds call
    assert r.unenriched_sports, "expected a sport to go un-enriched"
    for opp in r.arbs:
        assert all(l.quote_age_s is not None for l in opp.legs)
    assert r.reasons["unknown_age"] >= 1
    assert "NOT AGE-CHECKED" in r.summary()


def test_fits_budget_is_a_number_not_a_phrase():
    """The CLI gates on this. It used to gate on a substring of budget_advice()'s prose —
    a phrase that string never contained — so the guard silently never fired and a
    30-second polling loop ran anyway. Gate on the value, never on the sentence."""
    s = Scanner(Stub(), GateConfig())
    assert s.fits_budget(30) is False
    assert s.fits_budget(900) is False
    assert s.fits_budget(10800) is True
    assert s.credits_per_month(30) > s.credits_per_month(3600)


def test_advice_and_fits_budget_never_disagree():
    s = Scanner(Stub(), GateConfig())
    for interval in (30, 60, 300, 900, 3600, 10800, 86400):
        fits = s.fits_budget(interval)
        assert ("fits" in s.budget_advice(interval)) is fits, (
            "prose and gate disagree at %ds" % interval
        )


def test_estimate_and_advice_are_honest_about_the_free_tier():
    s = Scanner(Stub(), GateConfig())
    assert s.estimate_poll_cost(enriched_sports=2) == 5
    fast = s.budget_advice(60)
    assert "over" in fast or "x a" in fast
    assert "fits" in s.budget_advice(10800)


# ── the enrichment join ──────────────────────────────────────────────────────────────

def test_age_is_per_book_not_per_event():
    """One stale book must not condemn its fresh neighbour, and vice versa."""
    odds = {"afl": [{"id": "e1", "bookmakers": [
        {"key": "sportsbet", "age_seconds": 5}, {"key": "tab", "age_seconds": 9999}]}]}
    r = Scanner(Stub(arbs=[AFL_ARB], odds=odds), GateConfig()).poll()
    assert not r.arbs
    assert r.reasons["stale_quote"] == 1


def test_book_key_matching_is_case_insensitive():
    odds = {"afl": [{"id": "e1", "bookmakers": [
        {"key": "SportsBet", "age_seconds": 5}, {"key": "TAB", "age_seconds": 6}]}]}
    r = Scanner(Stub(arbs=[AFL_ARB], odds=odds), GateConfig()).poll()
    assert len(r.arbs) == 1


def test_book_missing_from_the_odds_payload_stays_unknown():
    """Absence must never be read as fresh."""
    odds = {"afl": [{"id": "e1", "bookmakers": [{"key": "sportsbet", "age_seconds": 5}]}]}
    r = Scanner(Stub(arbs=[AFL_ARB], odds=odds), GateConfig()).poll()
    assert not r.arbs
    assert r.reasons["unknown_age"] == 1


def test_book_with_no_age_field_stays_unknown():
    odds = {"afl": [{"id": "e1", "bookmakers": [
        {"key": "sportsbet"}, {"key": "tab", "age_seconds": 5}]}]}
    r = Scanner(Stub(arbs=[AFL_ARB], odds=odds), GateConfig()).poll()
    assert r.reasons["unknown_age"] == 1


def test_quality_age_is_the_fallback():
    odds = {"afl": [{"id": "e1", "bookmakers": [
        {"key": "sportsbet", "quality": {"age_seconds": 7}},
        {"key": "tab", "age_seconds": 5}]}]}
    r = Scanner(Stub(arbs=[AFL_ARB], odds=odds), GateConfig()).poll()
    assert len(r.arbs) == 1


# ── partial failure ──────────────────────────────────────────────────────────────────

def test_enrichment_failure_for_one_sport_does_not_leak_into_another():
    c = Stub(odds=FRESH, odds_error={"nrl": "boom"})
    r = Scanner(c, GateConfig()).poll()
    assert [o.sport for o in r.arbs] == ["afl"]
    assert "nrl" in r.unenriched_sports
    assert any("nrl" in e for e in r.errors)


def test_arb_fetch_failure_is_reported_not_swallowed():
    class Broken(Stub):
        def arb_sports(self, sport_key=None):
            raise RuntimeError("upstream down")

    r = Scanner(Broken(), GateConfig()).poll()
    assert r.errors and "upstream down" in r.errors[0]
    assert "Nothing found, but the poll had errors" in r.diagnosis()


def test_errors_carry_text_not_exception_objects():
    """A requests exception holds the response, whose request headers hold the API key."""
    class Broken(Stub):
        def arb_sports(self, sport_key=None):
            raise RuntimeError("pe_live_SHOULD_NOT_APPEAR")

    r = Scanner(Broken(), GateConfig()).poll()
    assert all(isinstance(e, str) for e in r.errors)


# ── anti-blindness ───────────────────────────────────────────────────────────────────

def test_zero_arbs_always_carries_a_reason():
    r = Scanner(Stub(arbs=[AFL_DUD], odds=FRESH), GateConfig()).poll()
    assert not r.arbs
    assert r.reasons
    assert "refused:" in r.summary()
    assert r.diagnosis()


def test_empty_feed_is_diagnosed_as_filter_or_quiet_market():
    r = Scanner(Stub(arbs=[]), GateConfig()).poll()
    assert "no candidates at all" in r.diagnosis()
    assert "sport filter" in r.diagnosis()


def test_all_unknown_age_is_diagnosed_as_broken_enrichment():
    r = Scanner(Stub(arbs=[AFL_ARB], odds={}), GateConfig()).poll()
    assert "unknown_age" in r.diagnosis()
    assert "Enrichment" in r.diagnosis()


def test_scope_refusal_is_named_as_scope_not_market():
    cfg = GateConfig(bettable_books={"nowhere"})
    r = Scanner(Stub(arbs=[AFL_ARB], odds=FRESH), GateConfig(bettable_books={"nowhere"})).poll()
    assert "scope decision, not a market condition" in r.diagnosis()


def test_unknown_age_allow_lets_an_unenriched_scan_through():
    cfg = GateConfig(unknown_age=UnknownAge.ALLOW)
    r = Scanner(Stub(arbs=[AFL_ARB], odds={}), cfg).poll()
    assert len(r.arbs) == 1
