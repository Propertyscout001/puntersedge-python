"""The gates: everything that decides a quoted arb is not worth acting on.

This module is the reason the package exists. Detection is already done server-side —
`/v1/arb/sports`, `/v1/arb/lines` and `/v1/arb/best-prices` return candidates. What they do
NOT do is tell you which candidates are artefacts. These gates are ported from the operator's
production arb engine, where each one was added after a specific class of phantom cost real
money or real credibility.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Two gates already run server-side and are NOT restated here:

  * `covers_outcome_space()` — an arb pays only if a stake sits on every way the market can
    settle. Enforced in `api/routes/arb.py`, including a hard three-way rule for soccer.
  * `is_self_arb()` — every leg on one exchange. Moot for customers in any case: Betfair is
    withheld from customer responses, so an exchange leg cannot appear in your feed.

Restating a gate in two places is precisely how the operator's sports alerter shipped
phantoms for months: "every gate lived on `/v1/arb/sports` but not on the alerter, or vice
versa." The two now share one definition each. Do not add a second copy here — if you think a
server gate is wrong, fix it server-side.

`single_venue` below may look like a duplicate of `is_self_arb`. It is not: it is stricter,
for a different reason, and the difference is documented on the gate itself.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, List, Mapping, Optional, Set

from .models import ArbKind, Opportunity, Verdict

# ── Refusal taxonomy ─────────────────────────────────────────────────────────────────
# The classification is the point, not the tokens.
#
#   policy — you declined on purpose. The count states your SCOPE. Shrinking it means
#            changing your mind about what you want to bet, not fixing anything.
#   data   — the opportunity arrived unusable. This is the count a feed fix reduces, and
#            the only one that belongs in a defect metric.
#
# Before the operator's engine separated these they were one undifferentiated number, and
# "the feed rejected 484 of 488 arbs" was read as a detection crisis when most of it was
# scope policy working exactly as intended.
REASON_CLASS = {
    # -- policy: declined on purpose --------------------------------------------------
    "book_not_bettable": "policy",  # you have no account there
    "below_min_edge":    "policy",  # smaller than you said you cared about
    "wrong_kind":        "policy",  # a shape this config does not scan
    # -- data: arrived unusable -------------------------------------------------------
    "bad_odds":          "data",    # a leg priced <= 1.0
    "no_cross":          "data",    # the prices do not actually cross
    "server_not_arb":    "data",    # the server declined to call this an arb
    "single_venue":      "data",    # every leg on one book
    "edge_ceiling":      "data",    # quoted edge implausibly large => stale/phantom
    "stale_quote":       "data",    # a leg older than the freshness budget
    "unknown_age":       "data",    # the feed did not say how old the price is
    "malformed":         "data",    # a gate could not be evaluated on this opp
}


def classify(reasons: Iterable[str]) -> str:
    """"clear" / "policy" / "data" for a set of refusal reasons.

    NOT a per-token lookup. An opp usually trips several conditions at once, so ANY policy
    reason makes the whole refusal policy: you would have declined it whatever the data
    looked like, and counting it as a data defect would inflate the defect number with
    opportunities you never wanted. Only when bad data is the SOLE obstacle is it a data
    defect — the conservative reading, and the one that actually predicts how many more
    arbs a feed fix would yield.

    An unregistered token counts as data, so a reason someone forgot to add here surfaces
    as a defect to investigate rather than being silently absorbed into "policy".
    """
    rs = list(reasons or [])
    if not rs:
        return "clear"
    if any(REASON_CLASS.get(r, "data") == "policy" for r in rs):
        return "policy"
    return "data"


class UnknownAge(str, Enum):
    """What to do when the feed does not say how old a price is.

    This is a required, explicit decision rather than a default buried in a boolean, because
    getting it wrong is invisible. `/v1/arb/sports` and `/v1/arb/lines` carry NO age field,
    so a freshness gate written the obvious way — `if age and age > budget` — passes every
    single price while appearing to check, forever, with no error. The operator's estate has
    a long list of monitors that went blind exactly this way.

    REJECT is the default. It means an unenriched scanner reads as "everything refused,
    reason `unknown_age`", which is loud, correct, and points straight at the fix: populate
    `Leg.quote_age_s` from `/v1/sports/{sport_key}/odds`, which does expose
    `data_age_seconds`, `freshest_age_seconds` and `stale` per event.
    """

    REJECT = "reject"  # safe default: no age, no bet
    ALLOW = "allow"    # you have another way to know the price is live


@dataclass
class GateConfig:
    """Thresholds. Every default here is defensible but none is universal — read the notes."""

    # Books you can actually place at. None means "no opinion, allow any". This is NOT the
    # engine's ARB_EXEC_BOOKS, which lists what the operator's automation can fill; for you
    # it means the books where you personally hold a funded account.
    bettable_books: Optional[Set[str]] = None

    # Ignore anything smaller than this quoted edge, in percent.
    min_edge_pct: float = 0.0

    # Implausibly large QUOTED edge => a stale or phantom price. The engine runs this over
    # racing at 8%; there is no equivalent ceiling anywhere on `/v1/arb/sports`, which is
    # why a stale leg can and does surface there as a 20-40% "arb". 0 disables the ceiling.
    #
    # Calibrate it yourself before trusting it: the right value is a property of the feed's
    # poll cadence and your books, not a constant. Sports polls every 900s.
    max_edge_pct: float = 8.0

    # Freshness budget in seconds. The sports feed's own `stale` flag trips at 1800s, which
    # is calibrated to "the poller has stopped", NOT to "this price is still there" — a
    # 15-minute-old h2h price is entirely capable of being gone. This default is far
    # tighter than the feed's flag on purpose.
    max_quote_age_s: float = 120.0

    unknown_age: UnknownAge = UnknownAge.REJECT

    # Shapes to consider. A kind outside this set is refused as `wrong_kind` (policy).
    kinds: Set[ArbKind] = field(
        default_factory=lambda: {ArbKind.SPORTS_BACK_BACK, ArbKind.LINES_BACK_BACK}
    )

    def __post_init__(self) -> None:
        # `bettable_books` is the drift attractor in this whole package: it is already a
        # book-keyed collection documented as "books where you hold a funded account", so
        # turning it into a credential holder is a one-token edit, Set[str] -> Dict[str,
        # creds]. Verified 2026-08-20: making that edit and passing
        # {"sportsbet": {"user": ..., "password": ...}} produces ZERO test failures and
        # ZERO refusals, because the gate does `set(opp.books).issubset(cfg.bettable_books)`
        # and issubset compares against dict KEYS. Three lines turn a silent migration into
        # an immediate failure.
        if isinstance(self.bettable_books, Mapping):
            raise TypeError(
                "bettable_books is a set of book NAMES, not a mapping. puntersedge stores "
                "only its own API key. It never stores bookmaker logins and never places "
                "bets — place your own bets yourself, in your own logged-in session."
            )

    @classmethod
    def from_env(cls, prefix: str = "PE_ARB_") -> "GateConfig":
        """Build from environment variables, treating a BLANK value as unset.

        A bare `FOO=` in a systemd unit or a `.env` has silently parsed as a real setting
        before and disabled the thing it was meant to configure. Blank means absent here.

        Deliberately an explicit list of five names, NOT a prefix scan. A scan would
        accept `PE_ARB_SPORTSBET_PASSWORD` silently, and `prefix` is caller-controlled.
        Do not generalise it.
        """

        def _get(name: str) -> Optional[str]:
            v = os.getenv(prefix + name)
            return None if v is None or not v.strip() else v.strip()

        cfg = cls()
        if (v := _get("BETTABLE_BOOKS")) is not None:
            cfg.bettable_books = {b.strip().lower() for b in v.split(",") if b.strip()}
        if (v := _get("MIN_EDGE_PCT")) is not None:
            cfg.min_edge_pct = float(v)
        if (v := _get("MAX_EDGE_PCT")) is not None:
            cfg.max_edge_pct = float(v)
        if (v := _get("MAX_QUOTE_AGE_S")) is not None:
            cfg.max_quote_age_s = float(v)
        if (v := _get("UNKNOWN_AGE")) is not None:
            cfg.unknown_age = UnknownAge(v.lower())
        return cfg

    @classmethod
    def load(cls, *, env=None, config_file=None) -> "GateConfig":
        """Build from the `[arb]` section of the config file, then the environment.

        Same file as the API key, two independent readers: this never looks at `api_key`,
        and the client never looks at `[arb]`.

        `PUNTERSEDGE_ARB_*` is the current spelling; `PE_ARB_*` is honoured as a
        deprecated alias. The alias cannot simply be dropped — it shipped in 0.2.0, and an
        unread gate variable does not raise, it reverts that gate to its default and
        quietly loosens what the user is willing to bet on.
        """
        from ..config import ARB_SECTION, ENV_PREFIX, ConfigChain

        chain = ConfigChain(env=env, config_file=config_file)
        cfg = cls()
        for name, coerce in _ARB_COERCE.items():
            found = chain.get(
                name,
                section=ARB_SECTION,
                env_names=(ENV_PREFIX + "ARB_" + name.upper(), "PE_ARB_" + name.upper()),
            )
            if found.value is None:
                continue
            try:
                setattr(cfg, name, coerce(found.value))
            except (TypeError, ValueError) as exc:
                # Name the setting AND its source: with two possible origins, a bare
                # "could not convert string to float: 'abc'" is close to useless.
                raise ValueError(
                    "bad puntersedge arb setting %s=%r: %s\n  (source: %s)"
                    % (name, found.value, exc, found.source)
                )
        return cfg


# One declarative coercion table, so the parsing exists once and `load()` and `from_env()`
# cannot drift apart on what a value means.
_ARB_COERCE = {
    "bettable_books": lambda v: {b.strip().lower() for b in v.split(",") if b.strip()},
    "min_edge_pct": float,
    "max_edge_pct": float,
    "max_quote_age_s": float,
    "unknown_age": lambda v: UnknownAge(v.strip().lower()),
}


def refusal_reasons(opp: Opportunity, cfg: Optional[GateConfig] = None) -> List[str]:
    """EVERY reason to refuse this opportunity, in a fixed order. Empty means take it.

    Deliberately total: a gate that raises is recorded as `malformed` rather than
    propagated. This runs in a list comprehension over a whole poll, and an AttributeError
    on one deformed opp must not be able to kill the tick and every good arb in it.
    Refusing that one opp WITH a reason is strictly safer than losing the batch.
    """
    cfg = cfg or GateConfig()
    out: List[str] = []

    def _chk(token: str, pred: Callable[[], bool]) -> None:
        """Append `token` if the predicate says refuse, or if it cannot be evaluated."""
        try:
            if pred():
                out.append(token)
        except Exception:
            if "malformed" not in out:
                out.append("malformed")

    _chk("wrong_kind", lambda: opp.kind not in cfg.kinds)

    # Odds sanity first: every downstream gate takes reciprocals, and this is what makes
    # that safe. Guarded exactly as the engine's `and` chain was.
    _chk("bad_odds", lambda: not opp.legs or any(leg.odds <= 1.0 for leg in opp.legs))

    if "bad_odds" not in out:
        # The crosses test. `/v1/arb/sports` and `/v1/arb/lines` are best-price-per-selection
        # PRICING feeds — they return every market they scan, including overround ones with
        # `arb_pct = 0`. Iterating the response and calling every row an arb mislabels the
        # plain market list as opportunities. This is the test that makes the count real.
        _chk("no_cross", lambda: opp.inv_sum() >= 1.0)

    # The server's own verdict, which is NOT redundant with `no_cross` and must not be
    # dropped as if it were. `/v1/arb/sports` zeroes `arb_pct` and sets `is_arb` false when
    # the priced selections fail `covers_outcome_space()` — and that test needs
    # `max_outcomes`, the fullest outcome space any single book listed, which is NOT in the
    # response. So there are rows whose best prices genuinely sum below 1.0 that are still
    # not arbs, and no amount of client-side arithmetic can tell you which. Recomputing
    # locally and ignoring this field resurrects the drawless-soccer phantom: 2.65 + 2.55
    # sums to 0.77 and reads as a 23% arb, but the draw loses both legs.
    #
    # Refuses unless `is_arb` is explicitly True — a missing field means the server did not
    # affirm it, which is the same thing as declining for the purpose of risking money.
    _chk("server_not_arb", lambda: opp.raw.get("is_arb") is not True)

    # Every leg on one book. STRICTER than the server's `is_self_arb()`, which refuses only
    # when all legs sit on one EXCHANGE, on the reasoning that a traditional bookmaker can
    # genuinely cross its own two-way market and both sides are bettable. That reasoning
    # holds for the operator, who can verify at the book. It does not transfer to you: a
    # same-book sub-1.0 sum you cannot verify is far more likely to be two prices read from
    # one board at different instants than a book genuinely crossing itself. Defaulting
    # strict is the safe asymmetry. Documented rather than silently differing, so that
    # nobody "reconciles" the two without knowing they disagree on purpose.
    _chk("single_venue", lambda: len(set(opp.books)) <= 1)

    _chk("below_min_edge",
         lambda: cfg.min_edge_pct > 0 and (opp.edge_pct or 0.0) < cfg.min_edge_pct)

    # Gated on the QUOTED edge, not a locally recomputed one: the whole purpose is to catch
    # a feed claiming an edge its own prices cannot support.
    _chk("edge_ceiling",
         lambda: cfg.max_edge_pct > 0 and (opp.edge_pct or 0.0) > cfg.max_edge_pct)

    # Freshness. Split into two distinct tokens on purpose — "this price is too old" and
    # "nobody told me how old this price is" are different problems with different fixes,
    # and merging them hides the second one completely.
    _chk("unknown_age",
         lambda: cfg.unknown_age is UnknownAge.REJECT
         and any(leg.quote_age_s is None for leg in opp.legs))
    _chk("stale_quote",
         lambda: cfg.max_quote_age_s > 0
         and any(leg.quote_age_s is not None and leg.quote_age_s > cfg.max_quote_age_s
                 for leg in opp.legs))

    _chk("book_not_bettable",
         lambda: bool(cfg.bettable_books)
         and not set(opp.books).issubset(cfg.bettable_books))

    return out


def evaluate(opp: Opportunity, cfg: Optional[GateConfig] = None) -> Verdict:
    """Everything a caller could want about one gate decision, in one call."""
    rs = refusal_reasons(opp, cfg)
    return Verdict(ok=not rs, reasons=rs, verdict_class=classify(rs))


def passes(opp: Opportunity, cfg: Optional[GateConfig] = None) -> bool:
    """True when no gate refuses. Defined as "no reason to refuse" so that the bool and the
    reasons can never drift apart."""
    return not refusal_reasons(opp, cfg)
