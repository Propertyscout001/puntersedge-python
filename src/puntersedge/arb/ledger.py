"""An append-only record of what you planned, what you actually got on, and what came back.

Three separate things, deliberately never conflated. The operator's own production ledger
conflated them and published wrong numbers for months; this module is shaped by those
specific failures.

A PLAN IS NOT A BET, AND AN ATTEMPT IS NOT A FILL
-------------------------------------------------
`size()` gives you a plan. Recording it as though money moved is the single most expensive
mistake available here. In the operator's engine an intermediate state was counted as a
placement and overstated real conversion by **3.6x** — 244 recorded attempts against 50
actual placements. So a plan recorded here contributes NOTHING to profit and loss until you
tell the ledger, per leg, what actually went on.

PARTIAL IS NOT AN ARB — IT IS A PUNT
------------------------------------
Book-vs-book means two or three bets that must ALL land. If one leg is refused, or the price
moves before you get on, you are not holding an arbitrage. You are holding a directional bet
you never intended to make. The ledger tracks placement per leg for exactly this reason, and
`pnl()` reports unhedged positions as a separate line that is never added into the arb
result. In the operator's own recount, the headline "+$57.52 profit" decomposed into +$6.74
of working arbitrage and a pile of naked positions that happened to win.

DEDUPLICATION HAPPENS ON READ, NOT ON WRITE
-------------------------------------------
The operator lost a published number to a double-write: a settlement was appended twice
because the writer's "already recorded" set was a stale snapshot taken before the append.
Every consumer summed rows raw, so one duplicate row in 98 moved four surfaces including the
public track record. The lesson is not "write more carefully" — it is that a reader which
deduplicates is immune to any writer bug, present or future. So every record carries a
stable identity, and `read()` collapses duplicates before anything sums them.

WHAT IS NEVER WRITTEN HERE
--------------------------
No API key, no environment snapshot, no exception objects, no bookmaker credentials of any
kind. Errors are recorded as text. This file is a record of your own bets and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Opportunity
from .sizing import Sizing

# Never the current working directory. A ledger written to CWD gets committed — the
# operator's own repo needed `*.jsonl` added to .gitignore, and that only protects one repo.
# XDG_STATE_HOME is the correct place for "state that should persist between restarts".
_DEFAULT_DIRNAME = "puntersedge"
_DEFAULT_FILENAME = "ledger.jsonl"

PLAN = "plan"
PLACEMENT = "placement"
SETTLEMENT = "settlement"
NOTE = "note"


def default_ledger_path(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """`$XDG_STATE_HOME/puntersedge/ledger.jsonl`, or None if there is no home directory.

    Resolved explicitly rather than via `expanduser`, for the same reason the config loader
    does: with HOME unset, `expanduser("~/x")` returns the string unchanged and a writer
    creates a directory literally named `~` in the working directory.
    """
    env = os.environ if env is None else env

    def _nb(name: str) -> Optional[str]:
        v = env.get(name)
        return v.strip() if v and v.strip() else None

    if os.name == "nt":
        base = _nb("LOCALAPPDATA") or _nb("APPDATA")
        return os.path.join(base, _DEFAULT_DIRNAME, _DEFAULT_FILENAME) if base else None
    base = _nb("XDG_STATE_HOME")
    if not base:
        home = _nb("HOME")
        base = os.path.join(home, ".local", "state") if home else None
    return os.path.join(base, _DEFAULT_DIRNAME, _DEFAULT_FILENAME) if base else None


def bet_id_for(opp: Opportunity, when: str) -> str:
    """A stable id for one intended position.

    Derived from the event, the legs and the timestamp you pass in, so re-recording the same
    plan produces the same id and `read()` collapses it. `when` is a caller-supplied string
    rather than a clock read, so the id is reproducible and testable.
    """
    parts = [opp.event_name or "", opp.sport or "", when]
    for leg in opp.legs:
        parts.append("%s|%s|%.4f" % (leg.book, leg.selection, leg.odds))
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return "arb_" + digest[:16]


@dataclass
class PlacedLeg:
    book: str
    selection: str
    stake: float
    odds: float
    returned: Optional[float] = None  # None until settled

    @property
    def settled(self) -> bool:
        return self.returned is not None


@dataclass
class Position:
    """One intended arb, folded from every record that mentions it."""

    bet_id: str
    event_name: str = ""
    sport: str = ""
    planned: List[Dict[str, Any]] = field(default_factory=list)
    placed: Dict[str, PlacedLeg] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @property
    def planned_books(self) -> List[str]:
        return [str(p.get("book", "")).strip().lower() for p in self.planned]

    @property
    def staked(self) -> float:
        return round(sum(l.stake for l in self.placed.values()), 10)

    @property
    def returned(self) -> float:
        return round(sum(l.returned or 0.0 for l in self.placed.values()), 10)

    @property
    def hedged(self) -> bool:
        """True only when EVERY planned leg was actually placed.

        The whole point of the module. A position missing a leg is directional.
        """
        return bool(self.planned) and set(self.planned_books) <= set(self.placed)

    @property
    def status(self) -> str:
        if not self.placed:
            return "planned"          # no money moved. Not a bet.
        if not self.hedged:
            return "unhedged"         # some legs on. This is a punt, not an arb.
        if all(l.settled for l in self.placed.values()):
            return "settled"
        return "open"

    @property
    def realised(self) -> Optional[float]:
        """Profit, or None while any placed leg is unsettled."""
        if not self.placed or not all(l.settled for l in self.placed.values()):
            return None
        return round(self.returned - self.staked, 10)

    @property
    def exposure(self) -> float:
        """Money at risk right now — what is staked and not yet settled."""
        return round(
            sum(l.stake for l in self.placed.values() if not l.settled), 10
        )


@dataclass
class PnL:
    """Profit and loss, decomposed by WHY the money arrived.

    `arb` is the only number that describes the strategy working. `unhedged` is what
    happened when a leg did not get on — it may well be positive, and it is still not
    arbitrage. Reporting one total for both is how a track record becomes untrue.
    """

    arb_profit: float = 0.0
    arb_count: int = 0
    arb_staked: float = 0.0
    unhedged_profit: float = 0.0
    unhedged_count: int = 0
    unhedged_staked: float = 0.0
    open_count: int = 0
    open_exposure: float = 0.0
    planned_only: int = 0
    duplicates_collapsed: int = 0

    @property
    def total_realised(self) -> float:
        """Every settled dollar, hedged or not. Present for completeness, and deliberately
        NOT the headline — see the class docstring."""
        return round(self.arb_profit + self.unhedged_profit, 10)

    @property
    def arb_roi_pct(self) -> float:
        return 100.0 * self.arb_profit / self.arb_staked if self.arb_staked else 0.0

    def summary(self) -> str:
        lines = [
            "arbitrage (all legs placed) : %+.2f over %d position%s, %.2f staked (%.2f%%)"
            % (self.arb_profit, self.arb_count, "" if self.arb_count == 1 else "s",
               self.arb_staked, self.arb_roi_pct),
        ]
        if self.unhedged_count:
            lines.append(
                "UNHEDGED (a leg missed)     : %+.2f over %d position%s, %.2f staked "
                "— directional bets, NOT arbitrage"
                % (self.unhedged_profit, self.unhedged_count,
                   "" if self.unhedged_count == 1 else "s", self.unhedged_staked)
            )
        if self.open_count:
            lines.append(
                "open                        : %d position%s, %.2f at risk"
                % (self.open_count, "" if self.open_count == 1 else "s", self.open_exposure)
            )
        if self.planned_only:
            lines.append(
                "planned, never placed       : %d — no money moved, excluded from P&L"
                % self.planned_only
            )
        if self.duplicates_collapsed:
            lines.append(
                "duplicate rows collapsed    : %d" % self.duplicates_collapsed
            )
        return "\n".join(lines)


class Ledger:
    """Append-only JSONL. Written once, folded on read."""

    def __init__(self, path: Optional[str] = None, *, env: Optional[Dict[str, str]] = None):
        resolved = path if path is not None else default_ledger_path(env)
        if resolved is None:
            raise ValueError(
                "No ledger path: HOME/XDG_STATE_HOME is unset, so there is nowhere "
                "sensible to write. Pass an explicit path."
            )
        self.path = os.fspath(resolved)

    # ── writing ──────────────────────────────────────────────────────────────────────
    def _append(self, record: Dict[str, Any]) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
            # makedirs' mode argument is masked by umask, so set it explicitly. Best effort:
            # a pre-existing directory we do not own should not make writing fail.
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        # O_APPEND and a single write() per record: concurrent scanners interleave whole
        # lines rather than corrupting one. 0600 at CREATION, never chmod-after-write —
        # that would leave the file world-readable for the length of the write.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def record_plan(self, opp: Opportunity, sizing: Sizing, *, when: str,
                    bet_id: Optional[str] = None) -> str:
        """Record an INTENDED position. This is not a bet and never counts toward P&L.

        Returns the bet_id to pass to `record_placement`.
        """
        bet_id = bet_id or bet_id_for(opp, when)
        self._append({
            "kind": PLAN,
            "bet_id": bet_id,
            "at": when,
            "event": opp.event_name,
            "sport": opp.sport,
            "legs": [
                {"book": l.book, "selection": l.selection,
                 "odds": l.odds, "planned_stake": l.stake}
                for l in sizing.legs
            ],
            "planned_profit": sizing.profit,
        })
        return bet_id

    def record_placement(self, bet_id: str, book: str, selection: str,
                         stake: float, odds: float, *, when: str) -> None:
        """Record a leg that ACTUALLY went on, at the stake and price you actually got.

        Stake and odds are what the book gave you, not what the plan asked for. They differ
        often enough that trusting the plan is how a ledger starts lying.
        """
        self._append({
            "kind": PLACEMENT, "bet_id": bet_id, "at": when,
            "book": book, "selection": selection,
            "stake": float(stake), "odds": float(odds),
        })

    def record_settlement(self, bet_id: str, book: str, returned: float, *,
                          when: str) -> None:
        """Record what a leg actually returned. 0 for a loser, stake for a push/void."""
        self._append({
            "kind": SETTLEMENT, "bet_id": bet_id, "at": when,
            "book": book, "returned": float(returned),
        })

    def record_note(self, bet_id: str, text: str, *, when: str) -> None:
        self._append({"kind": NOTE, "bet_id": bet_id, "at": when, "text": str(text)})

    # ── reading ──────────────────────────────────────────────────────────────────────
    def raw_rows(self) -> List[Dict[str, Any]]:
        """Every parseable line. A corrupt line is skipped, not fatal — a half-written row
        from a killed process must not make the whole history unreadable."""
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
        return out

    def read(self) -> Tuple[Dict[str, Position], int]:
        """Fold the log into positions. Returns (positions, duplicates_collapsed).

        Deduplication happens HERE, on read, and that is the whole design. A writer that
        appends the same settlement twice — which is exactly what happened in production,
        via a stale already-written set — cannot corrupt the total, because identical
        records collapse before anything is summed.
        """
        seen = set()
        duplicates = 0
        positions: Dict[str, Position] = {}
        for row in self.raw_rows():
            fingerprint = json.dumps(row, sort_keys=True, separators=(",", ":"))
            if fingerprint in seen:
                duplicates += 1
                continue
            seen.add(fingerprint)

            bet_id = str(row.get("bet_id") or "")
            if not bet_id:
                continue
            pos = positions.setdefault(bet_id, Position(bet_id=bet_id))
            kind = row.get("kind")
            if kind == PLAN:
                pos.event_name = row.get("event") or pos.event_name
                pos.sport = row.get("sport") or pos.sport
                pos.planned = list(row.get("legs") or [])
            elif kind == PLACEMENT:
                book = str(row.get("book", "")).strip().lower()
                if not book:
                    continue
                pos.placed[book] = PlacedLeg(
                    book=book,
                    selection=str(row.get("selection", "")),
                    stake=float(row.get("stake") or 0.0),
                    odds=float(row.get("odds") or 0.0),
                    returned=pos.placed[book].returned if book in pos.placed else None,
                )
            elif kind == SETTLEMENT:
                book = str(row.get("book", "")).strip().lower()
                if book in pos.placed:
                    pos.placed[book].returned = float(row.get("returned") or 0.0)
                else:
                    # A settlement for a leg with no placement. Recorded as a note rather
                    # than invented into a position: money cannot come back from a bet the
                    # ledger has no evidence was ever made.
                    pos.notes.append(
                        "settlement for unplaced leg %r (%s) — ignored in P&L"
                        % (book, row.get("returned"))
                    )
            elif kind == NOTE:
                pos.notes.append(str(row.get("text", "")))
        return positions, duplicates

    def positions(self) -> List[Position]:
        return list(self.read()[0].values())

    def pnl(self) -> PnL:
        """Profit and loss, with hedged and unhedged kept apart."""
        positions, duplicates = self.read()
        out = PnL(duplicates_collapsed=duplicates)
        for pos in positions.values():
            status = pos.status
            if status == "planned":
                out.planned_only += 1
            elif status == "open":
                out.open_count += 1
                out.open_exposure = round(out.open_exposure + pos.exposure, 10)
            elif status == "settled":
                out.arb_count += 1
                out.arb_profit = round(out.arb_profit + (pos.realised or 0.0), 10)
                out.arb_staked = round(out.arb_staked + pos.staked, 10)
            elif status == "unhedged":
                realised = pos.realised
                if realised is None:
                    out.open_count += 1
                    out.open_exposure = round(out.open_exposure + pos.exposure, 10)
                else:
                    out.unhedged_count += 1
                    out.unhedged_profit = round(out.unhedged_profit + realised, 10)
                    out.unhedged_staked = round(out.unhedged_staked + pos.staked, 10)
        return out
