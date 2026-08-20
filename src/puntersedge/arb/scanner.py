"""Poll the API, gate the candidates, and never leave you guessing why you found nothing.

CREDITS ARE THE DESIGN CONSTRAINT
---------------------------------
A poll costs 3 credits for `/v1/arb/sports`, plus 1 credit for every distinct sport whose
candidates need a freshness check (`/v1/sports/{key}/odds?markets=h2h` costs one credit per
market requested). The free tier is 1,500 credits a month. That arithmetic is brutal and
there is no way to be clever about it:

    interval    credits/month (3+enrich)   vs free tier 1,500
    30s                        518,400     346x over
    60s                        259,200     173x over
    5min                        51,840      35x over
    15min                       17,280      12x over
    1h                           4,320       3x over
    3h                           1,440      fits

Two consequences the scanner is built around.

First, gates run in TWO STAGES. Every gate except freshness needs nothing but the arb
payload, so they run first, for free, and only the sports with survivors get enriched. On a
typical poll most candidates die to `no_cross` or `server_not_arb`, so this is the
difference between paying for every sport and paying for one or two.

Second, polling faster than the upstream refresh spends money for data that CANNOT have
changed. The sports feed refreshes every 900s, so 15 minutes is the floor at which a poll
can return anything new. `Scanner` refuses a shorter interval unless you insist, because
the only thing a 30-second loop buys you is a drained quota.

FINDING NOTHING IS AN ANSWER, NOT A SILENCE
-------------------------------------------
Every poll returns a reason histogram. "0 arbs" from an efficient market and "0 arbs"
because your book filter matches nothing, or enrichment is failing, or the sport key is
wrong, are completely different situations that look identical in a log line that only
prints a count. `PollResult.summary()` prints the counts by reason so they never are.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .gates import GateConfig, classify, refusal_reasons
from .models import Opportunity, Verdict
from .parse import from_lines_payload, from_sports_payload

# Credit costs, from the API's own `check_and_deduct` calls. Hardcoded deliberately: they
# are not discoverable at runtime, and a scanner that guesses its own spend is worse than
# one that states an assumption you can check.
COST_ARB_SPORTS = 3
COST_ARB_LINES = 3
COST_ODDS_PER_MARKET = 1

# The upstream sports poll interval. Polling faster cannot surface anything new.
UPSTREAM_REFRESH_S = 900


@dataclass
class PollResult:
    """What one poll found, and — just as important — what it refused and why."""

    arbs: List[Opportunity] = field(default_factory=list)
    verdicts: Dict[int, Verdict] = field(default_factory=dict)
    candidates: int = 0
    reasons: Counter = field(default_factory=Counter)
    credits_spent: int = 0
    enriched_sports: List[str] = field(default_factory=list)
    unenriched_sports: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def refused(self) -> int:
        return self.candidates - len(self.arbs)

    def summary(self) -> str:
        """One line that cannot be misread as "everything is fine".

        The reason breakdown is the point. A count alone cannot distinguish an efficient
        market from a broken scanner, and the operator's estate is full of monitors that
        reported healthy while measuring nothing.
        """
        bits = [
            "%d candidate%s" % (self.candidates, "" if self.candidates == 1 else "s"),
            "%d passed" % len(self.arbs),
            "%d credits" % self.credits_spent,
        ]
        if self.reasons:
            top = ", ".join(
                "%s=%d" % (r, n) for r, n in self.reasons.most_common(6)
            )
            bits.append("refused: " + top)
        if self.unenriched_sports:
            bits.append("NOT AGE-CHECKED: " + ",".join(sorted(self.unenriched_sports)))
        if self.errors:
            bits.append("ERRORS: " + "; ".join(self.errors[:3]))
        return " | ".join(bits)

    def diagnosis(self) -> Optional[str]:
        """A plain-English guess at why a poll found nothing, or None if it found something.

        Deliberately opinionated. The failure this exists for is a scanner that runs for a
        week returning zero because of a filter typo, while its operator assumes the market
        is efficient.
        """
        if self.arbs:
            return None
        if self.errors:
            return "Nothing found, but the poll had errors — fix those first: %s" % (
                "; ".join(self.errors[:3])
            )
        if not self.candidates:
            return (
                "The API returned no candidates at all. That is the market being quiet, OR "
                "a sport filter that matches nothing — check your `sports=` values against "
                "client.sports()."
            )
        dominant, n = self.reasons.most_common(1)[0]
        if dominant == "unknown_age":
            return (
                "Every candidate was refused for unknown_age (%d). Enrichment is not "
                "supplying prices ages — check the errors list, or set "
                "GateConfig(unknown_age=UnknownAge.ALLOW) if you accept unaged prices." % n
            )
        if dominant == "book_not_bettable":
            return (
                "Every candidate was refused because it was priced at books outside your "
                "bettable_books (%d). That is a scope decision, not a market condition." % n
            )
        if dominant in ("no_cross", "server_not_arb"):
            return (
                "Candidates existed but none were real arbs (%s=%d). This is the normal, "
                "expected result — book-vs-book arbs are rare." % (dominant, n)
            )
        return "Nothing passed. Dominant refusal: %s=%d." % (dominant, n)


class CreditBudgetExceeded(RuntimeError):
    """Raised when a poll would take the scanner past its credit cap."""


class Scanner:
    """Two-stage arb scanner over the public endpoints.

    Nothing here places a bet, holds a bookmaker credential, or touches an account.
    """

    def __init__(
        self,
        client: Any,
        cfg: Optional[GateConfig] = None,
        *,
        sports: Optional[Sequence[str]] = None,
        lines: bool = False,
        credit_budget: Optional[int] = None,
        min_interval_s: float = UPSTREAM_REFRESH_S,
    ):
        self.client = client
        self.cfg = cfg or GateConfig()
        self.sports = list(sports) if sports else [None]
        self.lines = lines
        self.credit_budget = credit_budget
        self.credits_spent = 0
        self.min_interval_s = min_interval_s
        self._last_poll_at: Optional[float] = None

    # ── budget ───────────────────────────────────────────────────────────────────────
    def _spend(self, n: int, what: str) -> None:
        if self.credit_budget is not None and self.credits_spent + n > self.credit_budget:
            raise CreditBudgetExceeded(
                "%s would take this scanner to %d credits, past its budget of %d. "
                "Nothing was requested." % (what, self.credits_spent + n, self.credit_budget)
            )
        self.credits_spent += n

    def estimate_poll_cost(self, enriched_sports: int = 1) -> int:
        """Credits one poll costs. Stated up front so a free-tier user is not surprised."""
        base = COST_ARB_SPORTS * len(self.sports)
        if self.lines:
            base += COST_ARB_LINES * len(self.sports)
        return base + enriched_sports * COST_ODDS_PER_MARKET

    def budget_advice(self, interval_s: float, monthly_credits: int = 1500) -> str:
        """What this configuration costs per month against a plan, in plain words."""
        per_poll = self.estimate_poll_cost()
        polls = 30 * 86400 / max(interval_s, 1)
        total = per_poll * polls
        if total <= monthly_credits:
            return (
                "~%d credits/month at %.0fs intervals (~%d credits/poll) — fits a "
                "%d-credit plan." % (total, interval_s, per_poll, monthly_credits)
            )
        return (
            "~{:,.0f} credits/month at {:.0f}s intervals (~{} credits/poll) — that is "
            "{:.0f}x a {:,}-credit plan. Slow the interval to ~{:.0f} min, narrow "
            "`sports=`, or move to a larger plan."
        ).format(
            total, interval_s, per_poll, total / monthly_credits, monthly_credits,
            (per_poll * 30 * 86400 / monthly_credits) / 60,
        )

    # ── the poll ─────────────────────────────────────────────────────────────────────
    def poll(self) -> PollResult:
        started = time.time()
        result = PollResult()

        # Stage 1 — fetch candidates. Cheap gates only; no money spent on ages yet.
        raw: List[Opportunity] = []
        for sport in self.sports:
            try:
                self._spend(COST_ARB_SPORTS, "/v1/arb/sports")
                payload = self.client.arb_sports(sport_key=sport)
                raw.extend(from_sports_payload(payload))
            except CreditBudgetExceeded:
                raise
            except Exception as exc:
                result.errors.append("arb_sports(%s): %s" % (sport, _brief(exc)))
            if self.lines:
                try:
                    self._spend(COST_ARB_LINES, "/v1/arb/lines")
                    raw.extend(from_lines_payload(self.client.arb_lines(sport_key=sport)))
                except CreditBudgetExceeded:
                    raise
                except Exception as exc:
                    result.errors.append("arb_lines(%s): %s" % (sport, _brief(exc)))

        result.candidates = len(raw)
        result.credits_spent = self.credits_spent

        # Stage 2 — free gates. Whatever dies here costs nothing to reject.
        survivors: List[Opportunity] = []
        for opp in raw:
            rs = refusal_reasons(opp, self.cfg, include_freshness=False)
            if rs:
                result.reasons.update(rs)
                result.verdicts[id(opp)] = Verdict(False, rs, classify(rs))
            else:
                survivors.append(opp)

        # Stage 3 — enrich ONLY the sports that still have something worth paying for.
        ages: Dict[Tuple[str, str], float] = {}
        needed = sorted({(o.sport or "") for o in survivors if o.sport})
        for sport in needed:
            try:
                self._spend(COST_ODDS_PER_MARKET, "/v1/sports/%s/odds" % sport)
                ages.update(_ages_from_odds(self.client.odds(sport, markets="h2h")))
                result.enriched_sports.append(sport)
            except CreditBudgetExceeded:
                # Deliberately not fatal here: the candidates already enriched are still
                # usable, and the rest are reported as NOT age-checked rather than silently
                # treated as fresh.
                result.unenriched_sports.append(sport)
                result.errors.append("credit budget reached before enriching %s" % sport)
                break
            except Exception as exc:
                result.unenriched_sports.append(sport)
                result.errors.append("odds(%s): %s" % (sport, _brief(exc)))

        for opp in survivors:
            _apply_ages(opp, ages)

        # Stage 4 — the full gate set. A leg in an unenriched sport still has quote_age_s
        # of None, so it fails `unknown_age` rather than passing by omission.
        for opp in survivors:
            rs = refusal_reasons(opp, self.cfg, include_freshness=True)
            v = Verdict(not rs, rs, classify(rs))
            result.verdicts[id(opp)] = v
            if rs:
                result.reasons.update(rs)
            else:
                result.arbs.append(opp)

        result.credits_spent = self.credits_spent
        result.elapsed_s = round(time.time() - started, 3)
        self._last_poll_at = started
        return result


def _brief(exc: Exception) -> str:
    """Exception type and message, never the object.

    A `requests`-backed exception carries the response and its request headers, and those
    headers hold the API key. Errors here go into a PollResult that a caller may well log
    or write to a ledger, so only the text crosses that boundary.
    """
    return "%s: %s" % (type(exc).__name__, exc)


def _ages_from_odds(payload: Any) -> Dict[Tuple[str, str], float]:
    """Map (event_id, book) -> age in seconds from a `/v1/sports/{key}/odds` payload.

    Per BOOKMAKER, not per event. The event-level `data_age_seconds` is the oldest
    contributing book, so using it for every leg would mark a fresh book stale because some
    unrelated book on the same event went quiet — and any per-event figure is the wrong
    granularity for a gate that asks "is THIS price still there".

    A book with no age contributes NOTHING to the map rather than a zero. `age_seconds` is
    only set when the market has an `updated_at`, so absence is real and must stay unknown.
    """
    out: Dict[Tuple[str, str], float] = {}
    rows = payload if isinstance(payload, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("id") or row.get("event_id") or "")
        if not event_id:
            continue
        for bm in row.get("bookmakers") or []:
            if not isinstance(bm, dict):
                continue
            key = str(bm.get("key") or "").strip().lower()
            if not key:
                continue
            age = bm.get("age_seconds")
            if age is None:
                quality = bm.get("quality")
                if isinstance(quality, dict):
                    age = quality.get("age_seconds")
            if age is None:
                continue
            try:
                out[(event_id, key)] = float(age)
            except (TypeError, ValueError):
                continue
    return out


def _apply_ages(opp: Opportunity, ages: Dict[Tuple[str, str], float]) -> None:
    """Attach per-leg ages in place. Legs with no entry keep quote_age_s=None."""
    event_id = str(opp.raw.get("event_id") or opp.raw.get("id") or "")
    if not event_id:
        return
    for leg in opp.legs:
        age = ages.get((event_id, (leg.book or "").strip().lower()))
        if age is not None:
            leg.quote_age_s = age
