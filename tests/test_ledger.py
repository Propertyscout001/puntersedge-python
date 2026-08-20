"""Ledger correctness, framed as the three production failures it must not repeat.

1. A plan counted as a bet (overstated conversion 3.6x).
2. A partly-placed position counted as arbitrage (turned naked wins into a "track record").
3. A double-written settlement double-counted (moved a published figure by $10.50).
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from puntersedge.arb import ArbKind, Leg, Opportunity
from puntersedge.arb.ledger import Ledger, bet_id_for, default_ledger_path
from puntersedge.arb.sizing import size

T0 = "2026-08-20T10:00:00+00:00"


def opp(odds=(2.10, 2.10), books=("sportsbet", "tab")):
    return Opportunity(
        kind=ArbKind.SPORTS_BACK_BACK, event_name="Lions v Magpies", sport="afl",
        legs=[Leg(books[i], "sel%d" % i, o, quote_age_s=5.0) for i, o in enumerate(odds)],
        edge_pct=2.4, raw={"is_arb": True},
    )


@pytest.fixture
def led(tmp_path):
    return Ledger(str(tmp_path / "ledger.jsonl"))


def plan(led, o=None):
    o = o or opp()
    return led.record_plan(o, size(o, 100, step=1.0), when=T0)


# ── 1. a plan is not a bet ───────────────────────────────────────────────────────────

def test_a_plan_alone_moves_no_money():
    """The 3.6x overstatement: an intermediate state counted as a placement."""


def test_plan_only_is_excluded_from_pnl(led):
    plan(led)
    p = led.pnl()
    assert p.planned_only == 1
    assert p.arb_count == 0
    assert p.arb_profit == 0.0
    assert p.arb_staked == 0.0
    assert "no money moved" in p.summary()


def test_plan_status_is_planned(led):
    bid = plan(led)
    pos = {x.bet_id: x for x in led.positions()}[bid]
    assert pos.status == "planned"
    assert pos.staked == 0.0
    assert pos.realised is None


# ── 2. partial is a punt, not an arb ─────────────────────────────────────────────────

def test_one_leg_placed_is_unhedged_not_arbitrage(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_settlement(bid, "sportsbet", 105.0, when=T0)   # the leg WON
    p = led.pnl()
    assert p.arb_count == 0, "a single-leg position was counted as arbitrage"
    assert p.arb_profit == 0.0
    assert p.unhedged_count == 1
    assert p.unhedged_profit == pytest.approx(55.0)
    assert "NOT arbitrage" in p.summary()


def test_a_winning_naked_leg_never_flatters_the_arb_number(led):
    """The operator's own +$57.52 headline was mostly this."""
    for i in range(3):
        o = opp(books=("sportsbet", "tab"))
        o.event_name = "Game %d" % i
        bid = led.record_plan(o, size(o, 100, step=1.0), when=T0 + str(i))
        led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
        led.record_settlement(bid, "sportsbet", 105.0, when=T0)
    p = led.pnl()
    assert p.arb_profit == 0.0
    assert p.unhedged_profit == pytest.approx(165.0)
    assert p.total_realised == pytest.approx(165.0)


def test_both_legs_placed_and_settled_is_arbitrage(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_placement(bid, "tab", "sel1", 50.0, 2.10, when=T0)
    led.record_settlement(bid, "sportsbet", 105.0, when=T0)
    led.record_settlement(bid, "tab", 0.0, when=T0)
    p = led.pnl()
    assert p.arb_count == 1
    assert p.arb_profit == pytest.approx(5.0)
    assert p.arb_staked == pytest.approx(100.0)
    assert p.unhedged_count == 0


def test_hedged_but_unsettled_is_open_exposure(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_placement(bid, "tab", "sel1", 50.0, 2.10, when=T0)
    p = led.pnl()
    assert p.open_count == 1
    assert p.open_exposure == pytest.approx(100.0)
    assert p.arb_count == 0


def test_actual_stake_and_odds_are_recorded_not_the_plan(led):
    """What the book gave you, not what the plan asked for. They differ often."""
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 47.5, 2.05, when=T0)
    pos = {x.bet_id: x for x in led.positions()}[bid]
    assert pos.placed["sportsbet"].stake == 47.5
    assert pos.placed["sportsbet"].odds == 2.05


# ── 3. duplicates cannot double-count ────────────────────────────────────────────────

def test_a_double_written_settlement_is_collapsed(led):
    """The production bug: a stale already-written set appended the same row twice, and
    every consumer summed raw. One duplicate in 98 moved four surfaces."""
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_placement(bid, "tab", "sel1", 50.0, 2.10, when=T0)
    led.record_settlement(bid, "sportsbet", 105.0, when=T0)
    led.record_settlement(bid, "sportsbet", 105.0, when=T0)   # byte-identical duplicate
    led.record_settlement(bid, "tab", 0.0, when=T0)
    p = led.pnl()
    assert p.duplicates_collapsed == 1
    assert p.arb_profit == pytest.approx(5.0), "the duplicate was counted twice"


def test_duplicate_placements_do_not_double_the_stake(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    pos = {x.bet_id: x for x in led.positions()}[bid]
    assert pos.staked == pytest.approx(50.0)


def test_same_plan_recorded_twice_is_one_position(led):
    o = opp()
    s = size(o, 100, step=1.0)
    a = led.record_plan(o, s, when=T0)
    b = led.record_plan(o, s, when=T0)
    assert a == b
    assert len(led.positions()) == 1


def test_settlement_for_an_unplaced_leg_is_not_invented_into_profit(led):
    """Money cannot come back from a bet there is no evidence was made."""
    bid = plan(led)
    led.record_settlement(bid, "sportsbet", 999.0, when=T0)
    p = led.pnl()
    assert p.arb_profit == 0.0
    assert p.unhedged_profit == 0.0
    pos = {x.bet_id: x for x in led.positions()}[bid]
    assert any("unplaced leg" in n for n in pos.notes)


# ── file handling ────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are meaningless on Windows")
def test_ledger_is_created_0600(tmp_path):
    led = Ledger(str(tmp_path / "sub" / "ledger.jsonl"))
    plan(led)
    mode = stat.S_IMODE(os.stat(led.path).st_mode)
    assert mode == 0o600, "ledger created world-readable (mode %o)" % mode


def test_default_path_is_never_the_working_directory():
    """A ledger in CWD gets committed. It records real positions."""
    env = ({"LOCALAPPDATA": r"C:\\Users\\someone\\AppData\\Local"}
           if os.name == "nt" else {"HOME": "/home/someone"})
    p = default_ledger_path(env=env)
    assert p is not None
    assert os.path.isabs(p)
    assert "puntersedge" in p
    assert os.path.dirname(p) not in (".", os.getcwd())


def test_no_home_means_no_default_path_not_a_tilde_directory():
    assert default_ledger_path(env={}) is None
    with pytest.raises(ValueError, match="nowhere"):
        Ledger(env={})


def test_a_corrupt_line_does_not_destroy_the_history(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    with open(led.path, "a") as fh:
        fh.write("{not json at all\n")
    led.record_placement(bid, "tab", "sel1", 50.0, 2.10, when=T0)
    pos = {x.bet_id: x for x in led.positions()}[bid]
    assert len(pos.placed) == 2, "a half-written line made the rest unreadable"


def test_missing_file_reads_as_empty(tmp_path):
    led = Ledger(str(tmp_path / "nope.jsonl"))
    assert led.positions() == []
    assert led.pnl().arb_count == 0


def test_no_secret_shaped_field_is_ever_written(led):
    bid = plan(led)
    led.record_placement(bid, "sportsbet", "sel0", 50.0, 2.10, when=T0)
    led.record_note(bid, "manual check", when=T0)
    text = open(led.path).read().lower()
    for banned in ("api_key", "apikey", "x-api-key", "password", "token", "environ",
                   "secret", "authorization"):
        assert banned not in text, "ledger wrote a %r field" % banned
    for line in text.splitlines():
        assert set(json.loads(line)) <= {
            "kind", "bet_id", "at", "event", "sport", "legs", "planned_profit",
            "book", "selection", "stake", "odds", "returned", "text",
        }
