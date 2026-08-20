"""`puntersedge-arb` — scan, size, and keep an honest ledger, from a terminal.

Design notes that are not obvious:

* Nothing here ever prints the API key, or any part of it — not a prefix, not a hash. The
  `config` command reports where the key came from instead. A sha256 digest of a key was
  once emailed to two people labelled "YOUR API KEY", because a digest of a secret reads to
  the recipient as the secret.
* `scan` prints its credit cost before it spends anything, and `--watch` refuses an interval
  that would burn a free tier in days unless you say so explicitly. The failure this avoids
  is a user starting a 30-second loop and losing their month before lunch.
* Every exit is a distinct code so a shell can branch on it, and "found nothing" is NOT an
  error — it is the normal, expected outcome of scanning for arbs.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Sequence

EXIT_OK = 0
EXIT_NOTHING_FOUND = 0  # deliberately success: an efficient market is not a failure
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_BUDGET = 3
EXIT_INTERRUPT = 130


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gate_config(args):
    from .gates import GateConfig, UnknownAge

    cfg = GateConfig.load()
    if args.books:
        cfg.bettable_books = {b.strip().lower() for b in args.books.split(",") if b.strip()}
    if args.min_edge is not None:
        cfg.min_edge_pct = args.min_edge
    if args.max_age is not None:
        cfg.max_quote_age_s = args.max_age
    if args.allow_unaged:
        cfg.unknown_age = UnknownAge.ALLOW
    return cfg


def _client(args):
    from .. import PuntersEdge

    return PuntersEdge(config_file=args.config_file)


# ── scan ─────────────────────────────────────────────────────────────────────────────
def cmd_scan(args) -> int:
    from .scanner import CreditBudgetExceeded, Scanner
    from .sizing import size

    client = _client(args)
    cfg = _gate_config(args)
    sports = [s.strip() for s in args.sports.split(",")] if args.sports else None
    scanner = Scanner(
        client, cfg, sports=sports, lines=args.lines, credit_budget=args.budget
    )

    if args.watch and not args.yes:
        print("Credit cost of this configuration:\n  %s"
              % scanner.budget_advice(args.watch), file=sys.stderr)
        # Gate on the NUMBER, not on the wording of the sentence above it.
        if not scanner.fits_budget(args.watch) and args.watch < 900:
            print(
                "\nRefusing to poll every %ds. The upstream sports feed only refreshes "
                "every 900s, so a faster loop cannot surface anything new — it just spends "
                "credits. Use --watch 900 or higher, or pass --yes to override."
                % args.watch,
                file=sys.stderr,
            )
            return EXIT_CONFIG

    ledger = None
    if args.record:
        from .ledger import Ledger

        ledger = Ledger(args.ledger)

    found_any = False
    try:
        while True:
            try:
                result = scanner.poll()
            except CreditBudgetExceeded as exc:
                print("credit budget: %s" % exc, file=sys.stderr)
                return EXIT_BUDGET

            if args.json:
                print(json.dumps({
                    "at": _now(),
                    "candidates": result.candidates,
                    "passed": len(result.arbs),
                    "credits_spent": result.credits_spent,
                    "reasons": dict(result.reasons),
                    "errors": result.errors,
                    "arbs": [
                        {"event": o.event_name, "sport": o.sport, "edge_pct": o.edge_pct,
                         "legs": [{"book": l.book, "selection": l.selection,
                                   "odds": l.odds, "age_s": l.quote_age_s}
                                  for l in o.legs]}
                        for o in result.arbs
                    ],
                }, sort_keys=True))
            else:
                print("%s  %s" % (_now(), result.summary()))
                for opp in result.arbs:
                    print("  %s (%s) — quoted %.2f%%"
                          % (opp.event_name, opp.sport, opp.edge_pct))
                    plan = size(opp, args.stake, step=args.step)
                    if plan:
                        for leg in plan.legs:
                            print("      %-14s %-26s $%8.2f @ %.2f"
                                  % (leg.book, leg.selection, leg.stake, leg.odds))
                        print("      staked $%.2f -> guaranteed $%.2f (%.2f%%)"
                              % (plan.total_staked, plan.profit, plan.profit_pct))
                    else:
                        print("      not placeable: %s" % plan.reason)
                    if ledger is not None and plan:
                        bet_id = ledger.record_plan(opp, plan, when=_now())
                        print("      recorded as %s (a PLAN — no money has moved)" % bet_id)
                diag = result.diagnosis()
                if diag:
                    print("  %s" % diag)

            found_any = found_any or bool(result.arbs)
            if not args.watch:
                break
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nstopped. %d credits spent this session." % scanner.credits_spent,
              file=sys.stderr)
        return EXIT_INTERRUPT

    return EXIT_OK if found_any else EXIT_NOTHING_FOUND


# ── ledger ───────────────────────────────────────────────────────────────────────────
def cmd_ledger(args) -> int:
    from .ledger import Ledger

    ledger = Ledger(args.ledger)

    if args.ledger_cmd == "pnl":
        pnl = ledger.pnl()
        if args.json:
            print(json.dumps(pnl.__dict__, sort_keys=True, default=float))
        else:
            print(pnl.summary())
        return EXIT_OK

    if args.ledger_cmd == "positions":
        rows = sorted(ledger.positions(), key=lambda p: p.bet_id)
        if args.json:
            print(json.dumps([
                {"bet_id": p.bet_id, "event": p.event_name, "status": p.status,
                 "staked": p.staked, "realised": p.realised, "hedged": p.hedged}
                for p in rows
            ], sort_keys=True))
            return EXIT_OK
        if not rows:
            print("no positions recorded at %s" % ledger.path)
            return EXIT_OK
        for p in rows:
            flag = "  ⚠ UNHEDGED" if p.status == "unhedged" else ""
            realised = "" if p.realised is None else "  realised %+.2f" % p.realised
            print("%-20s %-9s staked %8.2f%s%s  %s"
                  % (p.bet_id, p.status, p.staked, realised, flag, p.event_name))
        return EXIT_OK

    if args.ledger_cmd == "place":
        ledger.record_placement(args.bet_id, args.book, args.selection,
                                args.stake, args.odds, when=_now())
        print("recorded placement: %s %s $%.2f @ %.2f"
              % (args.bet_id, args.book, args.stake, args.odds))
        return EXIT_OK

    if args.ledger_cmd == "settle":
        ledger.record_settlement(args.bet_id, args.book, args.returned, when=_now())
        print("recorded settlement: %s %s returned %.2f"
              % (args.bet_id, args.book, args.returned))
        return EXIT_OK

    if args.ledger_cmd == "path":
        print(ledger.path)
        return EXIT_OK

    return EXIT_ERROR


# ── config ───────────────────────────────────────────────────────────────────────────
def cmd_config(args) -> int:
    """Report where settings come from. Never prints the key itself."""
    from ..config import ConfigChain, default_config_path
    from .gates import GateConfig

    print("config file : %s" % (default_config_path() or "(no HOME — none)"))
    chain = ConfigChain(config_file=args.config_file)
    for line in chain.trace("api_key", env_names=("PUNTERSEDGE_API_KEY",)):
        print("  %s" % line)
    try:
        client = _client(args)
        print("\nresolved    : key from %s" % client.key_source)
        print("base_url    : %s" % client.base_url)
    except Exception as exc:
        print("\nNOT RESOLVED: %s" % exc)
        return EXIT_CONFIG

    cfg = GateConfig.load()
    print("\ngates:")
    print("  bettable_books  : %s" % (sorted(cfg.bettable_books) if cfg.bettable_books
                                      else "(any)"))
    print("  min_edge_pct    : %s" % cfg.min_edge_pct)
    print("  max_edge_pct    : %s" % cfg.max_edge_pct)
    print("  max_quote_age_s : %s" % cfg.max_quote_age_s)
    print("  unknown_age     : %s" % cfg.unknown_age.value)
    return EXIT_OK


# ── argument parsing ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="puntersedge-arb",
        description="Scan the PuntersEdge API for book-vs-book arbitrage, size the stakes, "
                    "and keep an honest ledger. It never places a bet and never holds a "
                    "bookmaker login.",
        epilog="No profit is claimed or implied. Australian bookmakers restrict accounts "
               "that arb, usually within weeks.",
    )
    p.add_argument("--config-file", help="config file to read instead of the default")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan for arbs once, or repeatedly with --watch")
    s.add_argument("--sports", help="comma-separated sport keys, e.g. afl,nrl")
    s.add_argument("--books", help="comma-separated books you can actually bet at")
    s.add_argument("--min-edge", type=float, help="ignore anything below this quoted %%")
    s.add_argument("--max-age", type=float, help="reject prices older than this many seconds")
    s.add_argument("--allow-unaged", action="store_true",
                   help="accept prices whose age is unknown (NOT recommended)")
    s.add_argument("--lines", action="store_true", help="also scan spreads/totals")
    s.add_argument("--stake", type=float, default=100.0,
                   help="total to lay out per arb, as a CAP (default 100)")
    s.add_argument("--step", type=float, default=1.0,
                   help="stake increment your books accept (default 1.0)")
    s.add_argument("--budget", type=int, help="hard cap on credits for this run")
    s.add_argument("--watch", type=float, metavar="SECONDS",
                   help="poll repeatedly at this interval")
    s.add_argument("--yes", action="store_true", help="skip the credit-cost confirmation")
    s.add_argument("--record", action="store_true",
                   help="write each sized arb to the ledger as a PLAN (no money moves)")
    s.add_argument("--ledger", help="ledger path (default: XDG state dir)")
    s.add_argument("--json", action="store_true", help="machine-readable output")
    s.set_defaults(func=cmd_scan)

    lg = sub.add_parser("ledger", help="inspect and update your bet ledger")
    lg.add_argument("--ledger", help="ledger path (default: XDG state dir)")
    lg.add_argument("--json", action="store_true")
    lsub = lg.add_subparsers(dest="ledger_cmd", required=True)
    lsub.add_parser("pnl", help="profit and loss, hedged and unhedged kept apart")
    lsub.add_parser("positions", help="every recorded position and its status")
    lsub.add_parser("path", help="print the ledger path")
    pl = lsub.add_parser("place", help="record a leg you actually got on")
    pl.add_argument("bet_id")
    pl.add_argument("book")
    pl.add_argument("selection")
    pl.add_argument("stake", type=float)
    pl.add_argument("odds", type=float)
    st = lsub.add_parser("settle", help="record what a leg returned")
    st.add_argument("bet_id")
    st.add_argument("book")
    st.add_argument("returned", type=float)
    lg.set_defaults(func=cmd_ledger)

    c = sub.add_parser("config", help="show where settings come from (never the key)")
    c.set_defaults(func=cmd_config)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    from ..exceptions import ConfigError, PuntersEdgeError

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print("config: %s" % exc, file=sys.stderr)
        return EXIT_CONFIG
    except PuntersEdgeError as exc:
        # Never the exception object: it carries the response, whose request headers hold
        # the API key. The message text is scrubbed by the client before it gets here.
        print("api: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_INTERRUPT


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
