"""Stake sizing, and the rounding that turns a real arb into a loss.

The headline test is `test_never_ships_a_losing_plan`. It is a property test over thousands
of generated arbs rather than a handful of cases, because the failure is arithmetic and
rare-ish (6.9% of thin arbs at whole-dollar stakes) — exactly the shape that slips past
example-based tests.
"""
from __future__ import annotations

import random

import pytest

from puntersedge.arb import ArbKind, Leg, Opportunity
from puntersedge.arb.sizing import minimum_viable_total, size, theoretical_split


def opp(odds, books=None):
    books = books or ["book%d" % i for i in range(len(odds))]
    return Opportunity(
        kind=ArbKind.SPORTS_BACK_BACK,
        event_name="e",
        sport="afl",
        legs=[Leg(books[i], "sel%d" % i, o, quote_age_s=5.0) for i, o in enumerate(odds)],
        edge_pct=1.0,
        raw={"is_arb": True},
    )


def naive_plan(odds, total, step=1.0):
    """Per-leg rounding — the obvious implementation this module exists to avoid.

    Returns (worst_case, total_staked). The total matters: naive rounding routinely stakes
    MORE than asked, so its profit is not comparable to a capped plan's without checking.
    """
    inv = sum(1.0 / o for o in odds)
    stakes = [round(total / (o * inv) / step) * step for o in odds]
    return min(s * o for s, o in zip(stakes, odds)) - sum(stakes), sum(stakes)


def naive_worst(odds, total, step=1.0):
    return naive_plan(odds, total, step)[0]


# ── the maths ────────────────────────────────────────────────────────────────────────

def test_theoretical_split_returns_the_same_whichever_leg_wins():
    odds = [2.10, 2.15, 2.20]
    stakes = theoretical_split(odds, 100.0)
    returns = [s * o for s, o in zip(stakes, odds)]
    assert max(returns) - min(returns) < 1e-9


def test_minimum_viable_total_is_the_binding_leg():
    odds, mins = [2.0, 4.0], [10.0, 10.0]
    need = minimum_viable_total(odds, mins)
    stakes = theoretical_split(odds, need)
    assert min(stakes) == pytest.approx(10.0, abs=1e-9)
    assert all(s >= 10.0 - 1e-9 for s in stakes)


# ── the reason this module exists ────────────────────────────────────────────────────

def test_never_ships_a_losing_plan():
    """A viable plan must never have a non-positive worst case.

    Measured on the same generator: naive per-leg rounding loses money on ~6.9% of these.
    """
    random.seed(11)
    viable = losing = naive_losing = 0
    for _ in range(4000):
        k = random.choice([2, 3])
        while True:
            odds = [round(random.uniform(2.0, 4.5), 2) for _ in range(k)]
            if 0.94 < sum(1.0 / o for o in odds) < 0.999:
                break
        total = random.choice([50, 100, 200])
        s = size(opp(odds), total, step=1.0)
        if s.viable:
            viable += 1
            if s.profit <= 0:
                losing += 1
        if naive_worst(odds, total) < 0:
            naive_losing += 1
    assert viable > 3000, "generator produced too few viable plans to be a real test"
    assert losing == 0, "shipped %d plans with a non-positive worst case" % losing
    # Pin the baseline this module beats — if this drops to 0 the test above is vacuous.
    assert naive_losing > 100, (
        "naive rounding lost money only %d times; the generator no longer produces the "
        "thin arbs this module is for" % naive_losing
    )


def test_beats_naive_rounding_on_a_majority_of_thin_arbs():
    """A RATE over qualifying samples, not a raw count.

    Counting raw hits makes the assertion depend on how many random draws happen to land in
    the thin-arb window, which is a property of the generator rather than of the code.

    Compared only against naive plans that RESPECT THE CAP. Naive rounding overspends the
    requested total in about 12% of these, and a plan that deploys more capital than it was
    allowed is not a legal alternative — comparing raw profit against it is comparing
    different outlays, not different algorithms.
    """
    random.seed(3)
    qualifying = better = worse = naive_over = 0
    for _ in range(4000):
        odds = [round(random.uniform(2.0, 4.0), 2) for _ in range(3)]
        if not (0.94 < sum(1.0 / o for o in odds) < 0.999):
            continue
        s = size(opp(odds), 100, step=1.0)
        if not s.viable:
            continue
        assert s.total_staked <= 100 + 1e-9, "staked past the cap"
        naive, naive_total = naive_plan(odds, 100)
        if naive_total > 100 + 1e-9:
            naive_over += 1
            continue  # not a legal plan at this cap
        qualifying += 1
        if s.profit > naive + 1e-9:
            better += 1
        elif s.profit < naive - 1e-9:
            worse += 1
    assert qualifying > 200, "only %d qualifying samples" % qualifying
    assert naive_over > 0, "generator no longer exercises the overspend case"
    assert worse == 0, (
        "naive beat the optimum %d times on a like-for-like cap — impossible" % worse
    )
    assert better / qualifying > 0.4, (
        "optimal beat naive on only %.1f%% of %d thin arbs"
        % (100 * better / qualifying, qualifying)
    )


def test_total_is_a_cap_and_is_never_exceeded():
    """You asked for X. It must never stake more than X.

    The first version of this module treated `total` as a target and overspent it in 41.1%
    of plans by a mean of $1.00 — a bankroll rule broken silently, which is the worst way
    for one to break.
    """
    random.seed(29)
    checked = under = 0
    for _ in range(2000):
        k = random.choice([2, 3])
        while True:
            odds = [round(random.uniform(2.0, 4.5), 2) for _ in range(k)]
            if 0.94 < sum(1.0 / o for o in odds) < 0.999:
                break
        cap = random.choice([37.0, 50.0, 100.0, 200.0])
        s = size(opp(odds), cap, step=1.0)
        if not s.viable:
            continue
        checked += 1
        assert s.total_staked <= cap + 1e-9, (
            "staked %.2f against a cap of %.2f" % (s.total_staked, cap)
        )
        if s.total_staked < cap - 1e-9:
            under += 1
    assert checked > 500
    assert under > 0, "never staked under the cap — is the cap being treated as a target?"


def brute_force_optimum(odds, cap, step=1.0):
    """Exhaustive search over the whole lattice. Only tractable for tiny cases — which is
    exactly why it belongs in a test and not in the module."""
    units = int(round(cap / step))
    best = -1e18
    if len(odds) == 2:
        for a in range(1, units + 1):
            for b in range(1, units - a + 1):
                best = max(best, min(a * step * odds[0], b * step * odds[1]) - (a + b) * step)
    else:
        for a in range(1, units + 1):
            for b in range(1, units - a + 1):
                for c in range(1, units - a - b + 1):
                    best = max(best, min(a * step * odds[0], b * step * odds[1],
                                         c * step * odds[2]) - (a + b + c) * step)
    return best


@pytest.mark.parametrize("k,n", [(2, 250), (3, 40)])
def test_matches_the_brute_force_optimum(k, n):
    """The binding-leg sweep must be exact, not merely good.

    The version this replaced enumerated the 2^N floor/ceil roundings of the ideal split.
    That is optimal for the total it lands on, but the optimum under a cap often sits at a
    SMALLER total where the lattice aligns better — it came in below the true optimum in
    54.3% of cases by a mean of $0.26. This test is the reason that cannot silently return.
    """
    rng = random.Random(77)
    compared = 0
    for _ in range(n):
        odds = [round(rng.uniform(2.0, 4.0), 2) for _ in range(k)]
        if not (0.93 < sum(1.0 / o for o in odds) < 0.999):
            continue
        cap = 60.0
        s = size(opp(odds), cap, step=1.0)
        best = brute_force_optimum(odds, cap, 1.0)
        mine = s.profit if s.viable else None
        if best <= 0:
            assert not s.viable, "claimed viable where no profitable plan exists"
            continue
        compared += 1
        assert mine is not None and mine >= best - 1e-9, (
            "odds %s: brute force found %.2f, size() returned %s" % (odds, best, mine)
        )
    assert compared > 5, "only %d comparable cases" % compared


def test_rounding_that_kills_the_edge_is_refused_not_returned():
    """A plan whose worst case is <= 0 must be viable=False, however good the theory was."""
    random.seed(5)
    found = False
    for _ in range(5000):
        odds = [round(random.uniform(2.0, 3.0), 2) for _ in range(2)]
        inv = sum(1.0 / o for o in odds)
        if not (0.990 < inv < 0.9999):
            continue
        s = size(opp(odds), 10, step=1.0)
        if not s.viable and "guaranteed loss" in s.reason:
            found = True
            assert s.profit <= 0
            break
    assert found, "never generated a case where rounding killed the edge"


def test_profit_is_the_worst_case_not_the_theoretical():
    s = size(opp([3.05, 3.10, 3.20]), 100, step=1.0)
    assert s.viable
    assert s.profit < s.theoretical_profit
    assert s.rounding_cost > 0
    assert s.profit == pytest.approx(min(l.ret for l in s.legs) - s.total_staked, abs=1e-9)


# ── book minimums ────────────────────────────────────────────────────────────────────

def test_below_minimum_is_refused_with_an_actionable_number():
    s = size(opp([2.10, 2.10], ["sportsbet", "tab"]), 4.0,
             minimums={"sportsbet": 5.0, "tab": 1.0})
    assert not s.viable
    assert "10.00" in s.reason  # the total that WOULD work


def test_scale_up_is_opt_in_only():
    kw = dict(minimums={"sportsbet": 5.0, "tab": 1.0})
    assert not size(opp([2.10, 2.10], ["sportsbet", "tab"]), 4.0, **kw).viable
    s = size(opp([2.10, 2.10], ["sportsbet", "tab"]), 4.0, scale_up_to_minimum=True, **kw)
    assert s.viable
    assert s.total_staked >= 10.0
    assert all(l.stake >= 5.0 or l.book == "tab" for l in s.legs)


def test_book_minimum_lookup_is_case_insensitive():
    s = size(opp([2.10, 2.10], ["SportsBet", "TAB"]), 4.0,
             minimums={"sportsbet": 5.0, "tab": 1.0})
    assert not s.viable, "minimums were not matched case-insensitively"


# ── refusals ─────────────────────────────────────────────────────────────────────────

def test_non_arb_is_refused():
    s = size(opp([1.90, 1.90]), 100)
    assert not s.viable
    assert "do not cross" in s.reason


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_total_refused(bad):
    assert not size(opp([2.1, 2.1]), bad).viable


def test_bad_odds_refused():
    assert not size(opp([1.0, 5.0]), 100).viable


def test_no_legs_refused():
    assert not size(opp([]), 100).viable


def test_sizing_is_falsey_when_not_viable():
    assert not size(opp([1.90, 1.90]), 100)
    assert size(opp([2.10, 2.10]), 100)


def test_refuses_an_absurd_number_of_legs():
    """2^N enumeration — cap it rather than hang on a deformed payload.

    Odds of 20.0 so the 13 legs genuinely cross (inv_sum 0.65). With 2.5 they would not,
    and the function would exit at the inv_sum check having never reached the cap — which
    is exactly the hole this test caught.
    """
    with pytest.raises(ValueError, match="refusing to size"):
        size(opp([20.0] * 13), 100)
