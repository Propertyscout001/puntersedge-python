"""CLI behaviour. The important assertions are about what it refuses and never prints."""
from __future__ import annotations

import json

import pytest

from puntersedge.arb.cli import EXIT_BUDGET, EXIT_CONFIG, EXIT_OK, build_parser, main

KEY = "pe_live_CLI_SENTINEL"


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "config"
    p.write_text("[puntersedge]\napi_key = %s\n" % KEY, encoding="utf-8")
    p.chmod(0o600)
    return str(p)


def test_parser_builds_and_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_help_does_not_promise_profit(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out.lower()
    assert "no profit is claimed" in out
    assert "never places a bet" in out


def test_config_reports_the_source_never_the_key(cfg, capsys, monkeypatch):
    monkeypatch.delenv("PUNTERSEDGE_API_KEY", raising=False)
    rc = main(["--config-file", cfg, "config"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert KEY not in out, "the CLI printed the API key"
    assert "api_key" in out          # the SOURCE line
    assert cfg in out


def test_config_without_a_key_fails_with_the_trace(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PUNTERSEDGE_API_KEY", raising=False)
    empty = tmp_path / "empty"
    empty.write_text("[arb]\nmin_edge_pct = 1\n", encoding="utf-8")
    empty.chmod(0o600)
    rc = main(["--config-file", str(empty), "config"])
    out = capsys.readouterr().out
    assert rc == EXIT_CONFIG
    assert "No PuntersEdge API key found" in out or "NOT RESOLVED" in out


def test_watch_refuses_a_wasteful_interval(cfg, capsys, monkeypatch):
    """Faster than the 900s upstream refresh cannot surface anything new."""
    monkeypatch.delenv("PUNTERSEDGE_API_KEY", raising=False)
    rc = main(["--config-file", cfg, "scan", "--watch", "30"])
    err = capsys.readouterr().err
    assert rc == EXIT_CONFIG
    assert "900s" in err
    assert "--yes" in err


def test_ledger_path_is_reported(tmp_path, capsys):
    target = str(tmp_path / "l.jsonl")
    rc = main(["ledger", "--ledger", target, "path"])
    assert rc == EXIT_OK
    assert target in capsys.readouterr().out


def test_ledger_pnl_on_an_empty_ledger(tmp_path, capsys):
    rc = main(["ledger", "--ledger", str(tmp_path / "l.jsonl"), "pnl"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "arbitrage" in out


def test_ledger_place_then_settle_then_pnl(tmp_path, capsys):
    lp = str(tmp_path / "l.jsonl")
    main(["ledger", "--ledger", lp, "place", "arb_x", "sportsbet", "Lions", "50", "2.10"])
    main(["ledger", "--ledger", lp, "settle", "arb_x", "sportsbet", "105"])
    capsys.readouterr()
    rc = main(["ledger", "--ledger", lp, "pnl"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    # No plan was recorded, so the position has no planned legs and cannot be "hedged".
    # It must therefore be reported as unhedged, never as arbitrage.
    assert "UNHEDGED" in out
    assert "NOT arbitrage" in out


def test_ledger_positions_json(tmp_path, capsys):
    lp = str(tmp_path / "l.jsonl")
    main(["ledger", "--ledger", lp, "place", "arb_x", "tab", "Magpies", "50", "2.0"])
    capsys.readouterr()
    main(["ledger", "--ledger", lp, "--json", "positions"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["bet_id"] == "arb_x"
    assert rows[0]["hedged"] is False


def test_scan_budget_exhaustion_exits_distinctly(cfg, capsys, monkeypatch):
    monkeypatch.delenv("PUNTERSEDGE_API_KEY", raising=False)
    rc = main(["--config-file", cfg, "scan", "--budget", "1"])
    assert rc == EXIT_BUDGET
    assert "credit budget" in capsys.readouterr().err
