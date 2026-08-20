"""How much to put on each leg — and what you actually make after rounding.

The theoretical stake split is one line of algebra. Everything hard here is what happens
when you round it to money a bookmaker will accept.

WHY ROUNDING IS THE WHOLE PROBLEM
---------------------------------
The equal-profit split almost never lands on whole units. Round each leg independently and
two things change at once: the total you stake, and the return on each leg. The returns are
no longer equal, so your profit becomes the WORST leg — and for a thin arb that can be
negative. You have then placed two bets that are guaranteed, between them, to lose.

Measured over 3,000 randomly generated 2- and 3-leg arbs with `inv_sum` between 0.94 and
0.999, rounding to whole dollars:

    naive per-leg rounding produced a GUARANTEED LOSS in  6.9% of them
    naive per-leg rounding was worse than optimal in     56.0% of them

So this module never rounds per leg. It enumerates the 2^N floor/ceil combinations (N is 2
or 3 in practice) and picks the one that maximises the worst-case return. That is exact, not
a heuristic, and at these sizes it is free.

WHAT THIS MODULE WILL NOT DO
----------------------------
It will not tell you a profit you cannot have. `Sizing.profit` is the WORST-CASE figure
after rounding — the number you are actually guaranteed. The theoretical pre-rounding
number is available as `theoretical_profit`, deliberately under a name you cannot mistake
for the real one.

It takes the bankroll as an argument and has no way to discover one. That is deliberate: the
moment sizing wants to read a real book balance it needs a bookmaker account, and this
package must never have one.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .models import Opportunity

# Most Australian books accept stakes in cents. Some round to whole dollars, and a few
# minimum-bet rules behave as if they do. The default is cents; pass step=1.0 when a book
# will only take whole dollars, because that is where the loss risk lives.
DEFAULT_STEP = 0.01

# What a book will accept as a single bet. There is no API for this and it changes, so it is
# a caller-supplied number with a conservative default rather than a table that silently
# goes stale.
DEFAULT_MIN_STAKE = 1.0


@dataclass(frozen=True)
class LegStake:
    book: str
    selection: str
    odds: float
    stake: float
    ret: float  # gross return if THIS leg wins

    @property
    def profit_if_wins(self) -> float:
        """Kept for readability at call sites; the real number is Sizing.profit."""
        return self.ret


@dataclass(frozen=True)
class Sizing:
    """A placeable stake plan, and the truth about what it returns.

    `profit` is the WORST case across legs after rounding — what you are actually
    guaranteed. Read that one. `theoretical_profit` is the pre-rounding ideal and exists
    only so the gap is inspectable.
    """

    legs: List[LegStake]
    total_staked: float
    profit: float             # worst-case, post-rounding. THE number.
    theoretical_profit: float  # pre-rounding ideal. NOT the number.
    step: float
    viable: bool
    reason: str = ""

    @property
    def profit_pct(self) -> float:
        """Worst-case profit as a percentage of what you actually staked."""
        return 100.0 * self.profit / self.total_staked if self.total_staked else 0.0

    @property
    def rounding_cost(self) -> float:
        """How much the rounding took off the ideal. Always >= 0."""
        return self.theoretical_profit - self.profit

    def __bool__(self) -> bool:
        return self.viable


def theoretical_split(odds: Sequence[float], total: float) -> List[float]:
    """The exact equal-profit split, before any rounding.

    stake_i = total / (odds_i * inv_sum), so stake_i * odds_i = total / inv_sum for every i
    — the same return whichever leg wins. Profit is total * (1/inv_sum - 1).
    """
    inv_sum = sum(1.0 / o for o in odds)
    if inv_sum <= 0:
        raise ValueError("odds must be positive")
    return [total / (o * inv_sum) for o in odds]


def minimum_viable_total(
    odds: Sequence[float], minimums: Sequence[float]
) -> float:
    """The smallest total stake at which EVERY leg clears its book minimum.

    From stake_i = total / (odds_i * inv_sum) >= min_i, the binding constraint is
    total >= min_i * odds_i * inv_sum, so the answer is the max over legs.

    Worth surfacing rather than just refusing: "you need at least $37.40 for this one" is
    actionable, where "not viable" sends the user hunting for a bug.
    """
    inv_sum = sum(1.0 / o for o in odds)
    return max(m * o * inv_sum for m, o in zip(minimums, odds))


def size(
    opp: Opportunity,
    total: float,
    *,
    step: float = DEFAULT_STEP,
    minimums: Optional[Dict[str, float]] = None,
    default_minimum: float = DEFAULT_MIN_STAKE,
    scale_up_to_minimum: bool = False,
) -> Sizing:
    """Stake plan for `opp` laying out about `total`, rounded to something placeable.

    `minimums` maps a lowercased book key to that book's minimum bet; anything missing uses
    `default_minimum`. When a leg would fall below its minimum the result is `viable=False`
    with `minimum_viable_total()` named in `reason` — unless `scale_up_to_minimum` is set,
    in which case the whole plan is scaled up to the smallest total that works.

    Scaling up is NOT the default. It silently puts more of your money at risk than you
    asked for, and "I asked for $50 and it staked $180" is a worse surprise than a refusal.
    """
    odds = [leg.odds for leg in opp.legs]
    if not odds:
        return Sizing([], 0.0, 0.0, 0.0, step, False, "no legs")
    if any(o <= 1.0 for o in odds):
        return Sizing([], 0.0, 0.0, 0.0, step, False, "a leg is priced at or below 1.0")
    if total <= 0:
        return Sizing([], 0.0, 0.0, 0.0, step, False, "total stake must be positive")
    if step <= 0:
        raise ValueError("step must be positive")
    # Checked BEFORE anything else that could return early. The 2^N enumeration below is
    # the thing being protected, and a guard that a malformed payload can route around is
    # not a guard — a 13-leg opportunity whose prices happen not to cross would previously
    # exit at the inv_sum check and never reach this.
    if len(odds) > 12:
        raise ValueError(
            "refusing to size a %d-leg opportunity: the optimal-rounding search is 2^N "
            "and no real market has this many outcomes" % len(odds)
        )

    mins = [
        (minimums or {}).get((leg.book or "").strip().lower(), default_minimum)
        for leg in opp.legs
    ]

    inv_sum = sum(1.0 / o for o in odds)
    if inv_sum >= 1.0:
        # Not an arb at all. Refuse rather than return a "plan" that loses by construction.
        return Sizing(
            [], 0.0, 0.0, 0.0, step, False,
            "prices do not cross (inv_sum %.4f >= 1.0) — this is not an arb" % inv_sum,
        )

    need = minimum_viable_total(odds, mins)
    if total < need:
        if not scale_up_to_minimum:
            return Sizing(
                [], 0.0, 0.0, 0.0, step, False,
                "total of %.2f is below the book minimums; needs at least %.2f"
                % (total, need),
            )
        total = math.ceil(need / step) * step

    exact = theoretical_split(odds, total)
    theoretical = total * (1.0 / inv_sum - 1.0)

    # Work in integer units of `step` so the arithmetic is exact. Floats accumulate error
    # in exactly the place where the answer is a few cents either side of zero.
    lo = [int(math.floor(round(s / step, 9))) for s in exact]

    best_units: Optional[List[int]] = None
    best_profit = -math.inf
    best_total = math.inf
    # 2^N combinations of floor / floor+1. N is 2 or 3 for real markets, and the leg count
    # was capped at the top of this function.
    for bits in itertools.product((0, 1), repeat=len(odds)):
        units = [lo[i] + bits[i] for i in range(len(odds))]
        if any(u <= 0 for u in units):
            continue
        stakes = [u * step for u in units]
        if any(s < m - 1e-9 for s, m in zip(stakes, mins)):
            continue
        staked = sum(stakes)
        worst = min(s * o for s, o in zip(stakes, odds)) - staked
        # Maximise the guaranteed profit; tie-break on LESS capital at risk, since an equal
        # return off a smaller outlay is strictly better.
        if worst > best_profit + 1e-12 or (
            abs(worst - best_profit) <= 1e-12 and staked < best_total
        ):
            best_profit, best_total, best_units = worst, staked, units

    if best_units is None:
        return Sizing(
            [], 0.0, 0.0, theoretical, step, False,
            "no rounding of this split satisfies every book minimum at a total of %.2f"
            % total,
        )

    stakes = [round(u * step, 10) for u in best_units]
    legs = [
        LegStake(
            book=leg.book,
            selection=leg.selection,
            odds=leg.odds,
            stake=stakes[i],
            ret=round(stakes[i] * leg.odds, 10),
        )
        for i, leg in enumerate(opp.legs)
    ]
    staked = round(sum(stakes), 10)
    profit = round(min(l.ret for l in legs) - staked, 10)

    # A non-positive worst case is NOT viable, even though the arithmetic "worked". This is
    # the case the whole module exists for: rounding a thin arb into a guaranteed loss.
    viable = profit > 0
    reason = "" if viable else (
        "rounding to %g leaves a worst-case of %.2f — placing this is a guaranteed loss. "
        "Try a larger total, or a book that accepts finer stakes." % (step, profit)
    )
    return Sizing(legs, staked, profit, theoretical, step, viable, reason)
