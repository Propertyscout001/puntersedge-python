"""Tell someone when an arb survives the gates — without flooding them.

Two rules here are not style preferences. Each is a production incident in this estate.

RULE 1 — NEVER DEDUPLICATE BY THE TEXT YOU SEND
-----------------------------------------------
A monitor elsewhere was written to alert on state change, then at most once every 12 hours.
Correct policy, correctly tested, and it still sent ~24 identical emails a day for three days.
The throttle compared the rendered alert strings, and the string contained a live counter —
"last ingest 66h ago" became "67h" an hour later. The message differed every run, so "has this
changed?" was always true and the 12-hourly throttle never engaged once.

So `alert_identity()` contains **no number that moves**: no odds, no edge, no age, no stake,
no timestamp, no count. It is the event and the legs, and nothing else. Whether to re-alert an
identity is decided by a clock and an explicit threshold, never by comparing messages.

RULE 2 — PEEK, THEN RECORD. COUNT WHAT YOU SENT, NOT WHAT YOU TRIED
-------------------------------------------------------------------
A rate limiter elsewhere recorded the event before counting it. Two failures followed: calls
that did nothing still burned quota, and — the nasty one — a *rejected* call recorded itself,
so every retry pushed its own unblock time further out and the window could never be waited
out. The cap said "alerts sent"; the counter answered "times someone asked".

So `AlertPolicy.allows()` only reads state, and `record_sent()` is called exactly once, at the
site where a send actually succeeded. A suppressed alert, a failed webhook, and a dry run all
consume nothing.

WHAT IS NEVER SENT
------------------
No API key, and no webhook URL — a webhook URL is itself a credential, since anyone holding it
can post to that channel. It is wrapped so it cannot be printed by accident, and it never
appears in a message, a log line, an error, or the ledger.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from .._secret import Secret
from ..exceptions import ConfigError
from .models import Opportunity
from .sizing import Sizing

DEFAULT_COOLDOWN_S = 3600.0
DEFAULT_MAX_PER_HOUR = 20


def alert_identity(opp: Opportunity) -> str:
    """A stable key for "this arb", containing nothing that moves.

    Deliberately excludes odds, edge, age and stake. If any of those were in here, the same
    opportunity would present a new identity on almost every poll and every throttle built on
    it would silently never engage — which is exactly how the flood referenced above
    happened. Whether a known identity is worth re-alerting is a decision for the clock and
    an explicit improvement threshold, both of which live in `AlertPolicy`.
    """
    legs = sorted(
        "%s|%s" % ((leg.book or "").strip().lower(), (leg.selection or "").strip().lower())
        for leg in opp.legs
    )
    raw = "::".join([(opp.sport or "").strip().lower(),
                     (opp.event_name or "").strip().lower()] + legs)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def render(opp: Opportunity, sizing: Optional[Sizing] = None) -> str:
    """The human-readable alert. Free to contain numbers — it is never used for dedup."""
    lines = ["%s (%s) — %.2f%% quoted" % (opp.event_name, opp.sport, opp.edge_pct)]
    for leg in opp.legs:
        age = "" if leg.quote_age_s is None else "  [%.0fs old]" % leg.quote_age_s
        lines.append("  %-14s %-24s @ %.2f%s" % (leg.book, leg.selection, leg.odds, age))
    if sizing is not None and sizing.viable:
        lines.append("  stake $%.2f -> guaranteed $%.2f (%.2f%%)"
                     % (sizing.total_staked, sizing.profit, sizing.profit_pct))
        for leg in sizing.legs:
            lines.append("    $%8.2f on %s at %s" % (leg.stake, leg.selection, leg.book))
    elif sizing is not None:
        lines.append("  not placeable: %s" % sizing.reason)
    lines.append("  place these yourself — puntersedge never bets for you")
    return "\n".join(lines)


# ── delivery ─────────────────────────────────────────────────────────────────────────
class Notifier:
    """Somewhere an alert can go. `send` returns True only if it actually got there."""

    name = "notifier"

    def send(self, text: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    """The default. Sends nothing anywhere, so it cannot leak or spam."""

    name = "console"

    def __init__(self, stream=None):
        self._stream = stream

    def send(self, text: str) -> bool:
        import sys

        print(text, file=self._stream or sys.stdout)
        return True


class NullNotifier(Notifier):
    """Renders and discards. What `--dry-run` uses, so a policy can be exercised for real
    without anything leaving the machine."""

    name = "null"

    def __init__(self):
        self.sent: List[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


class WebhookNotifier(Notifier):
    """POST to a Discord/Slack-compatible incoming webhook.

    The URL is a bearer credential — anyone holding it can post to that channel — so it is
    wrapped in `Secret` and never rendered. Errors from this class quote the status code and
    the host, never the URL.
    """

    name = "webhook"

    def __init__(self, url: str, *, timeout: float = 10.0, session: Any = None,
                 field: str = "content"):
        parts = urlsplit(url or "")
        if parts.scheme != "https":
            # A webhook URL is a credential. http would put it, and everything posted
            # through it, on the wire in clear.
            raise ConfigError(
                "alert webhook must be an https URL (got scheme %r)." % (parts.scheme,)
            )
        self._url = Secret(url)
        self._host = parts.hostname or "?"
        self.timeout = timeout
        self.field = field
        self._session = session

    def __repr__(self) -> str:
        return "WebhookNotifier(host=%r)" % self._host

    def send(self, text: str) -> bool:
        import requests

        session = self._session or requests
        try:
            resp = session.post(
                self._url.reveal(),               # audited reveal: the only one
                json={self.field: text},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise AlertDeliveryError(
                "webhook to %s failed: %s: %s" % (self._host, type(exc).__name__, exc)
            )
        status = getattr(resp, "status_code", 0)
        if not (200 <= status < 300):
            raise AlertDeliveryError(
                "webhook to %s returned %s" % (self._host, status)
            )
        return True


class AlertDeliveryError(RuntimeError):
    """Delivery failed. Carries a host and a status, never a URL."""


# ── policy ───────────────────────────────────────────────────────────────────────────
@dataclass
class _Seen:
    last_sent_at: float
    last_edge_pct: float


@dataclass
class AlertPolicy:
    """Decides whether an identity may be alerted right now. Reads state; never writes it.

    `allows()` is a pure peek and `record_sent()` is the only mutation, called once at the
    site where a send succeeded. That split is the whole point — see RULE 2 in the module
    docstring for the incident that produced it.
    """

    cooldown_s: float = DEFAULT_COOLDOWN_S
    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    # Re-alert inside the cooldown only if the edge improved by at least this many points.
    # None disables it. An explicit threshold crossing, deliberately not a comparison of
    # rendered text or a raw "did the number change".
    realert_on_improvement_pct: Optional[float] = None

    _seen: Dict[str, _Seen] = field(default_factory=dict)
    _sent_at: List[float] = field(default_factory=list)

    def allows(self, identity: str, now: float, edge_pct: float = 0.0) -> Optional[str]:
        """None if it may be sent, otherwise the reason it may not.

        A reason string rather than a bool, so a scanner can report WHY it went quiet. An
        alerter that has silently suppressed everything for an hour looks exactly like a
        market with no arbs in it.
        """
        recent = [t for t in self._sent_at if now - t < 3600.0]
        if self.max_per_hour and len(recent) >= self.max_per_hour:
            return "rate_capped"
        prior = self._seen.get(identity)
        if prior is None:
            return None
        elapsed = now - prior.last_sent_at
        if elapsed >= self.cooldown_s:
            return None
        if (self.realert_on_improvement_pct is not None
                and edge_pct - prior.last_edge_pct >= self.realert_on_improvement_pct):
            return None
        return "cooldown"

    def record_sent(self, identity: str, now: float, edge_pct: float = 0.0) -> None:
        """Call ONLY after a send actually succeeded."""
        self._seen[identity] = _Seen(last_sent_at=now, last_edge_pct=edge_pct)
        self._sent_at.append(now)
        # Trim on write so the list cannot grow without bound in a long-running scanner.
        self._sent_at = [t for t in self._sent_at if now - t < 3600.0]

    # ── persistence ──────────────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "seen": {k: {"last_sent_at": v.last_sent_at, "last_edge_pct": v.last_edge_pct}
                     for k, v in self._seen.items()},
            "sent_at": list(self._sent_at),
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restore state. Without this a restart re-alerts everything it already sent —
        which turns a crash loop into a flood."""
        for key, value in (data.get("seen") or {}).items():
            try:
                self._seen[key] = _Seen(
                    last_sent_at=float(value["last_sent_at"]),
                    last_edge_pct=float(value.get("last_edge_pct") or 0.0),
                )
            except (TypeError, ValueError, KeyError):
                continue
        for t in data.get("sent_at") or []:
            try:
                self._sent_at.append(float(t))
            except (TypeError, ValueError):
                continue


@dataclass
class AlertResult:
    sent: int = 0
    suppressed: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = ["%d sent" % self.sent]
        if self.suppressed:
            bits.append("suppressed: " + ", ".join(
                "%s=%d" % (k, v) for k, v in sorted(self.suppressed.items())))
        if self.errors:
            bits.append("delivery errors: %d (%s)" % (len(self.errors), self.errors[0]))
        return " | ".join(bits)


class Alerter:
    """Renders, throttles and delivers. Never lets a delivery problem stop a scan."""

    def __init__(self, notifier: Notifier, policy: Optional[AlertPolicy] = None,
                 *, min_edge_pct: float = 0.0):
        self.notifier = notifier
        self.policy = policy or AlertPolicy()
        self.min_edge_pct = min_edge_pct

    def notify(self, opps: Sequence[Opportunity], now: float,
               sizer: Optional[Callable[[Opportunity], Sizing]] = None) -> AlertResult:
        result = AlertResult()
        for opp in opps:
            if self.min_edge_pct and (opp.edge_pct or 0.0) < self.min_edge_pct:
                result.suppressed["below_min_edge"] = \
                    result.suppressed.get("below_min_edge", 0) + 1
                continue
            identity = alert_identity(opp)
            reason = self.policy.allows(identity, now, opp.edge_pct or 0.0)
            if reason:
                result.suppressed[reason] = result.suppressed.get(reason, 0) + 1
                continue

            sizing = None
            if sizer is not None:
                try:
                    sizing = sizer(opp)
                except Exception as exc:
                    result.errors.append("sizing: %s: %s" % (type(exc).__name__, exc))

            try:
                delivered = self.notifier.send(render(opp, sizing))
            except AlertDeliveryError as exc:
                # Text only — never the exception object, which for a requests-backed
                # failure carries the response and its request headers.
                result.errors.append(str(exc))
                continue
            except Exception as exc:
                result.errors.append("%s: %s" % (type(exc).__name__, exc))
                continue

            if delivered:
                # RECORD, and only here. A failure above consumed no quota and did not
                # extend anyone's cooldown.
                self.policy.record_sent(identity, now, opp.edge_pct or 0.0)
                result.sent += 1
            else:
                result.errors.append("%s reported the alert was not delivered"
                                     % self.notifier.name)
        return result


def notifier_from_config(webhook_url: Optional[str], *, dry_run: bool = False,
                         console: bool = False) -> Notifier:
    """Pick a delivery target. Defaults to doing nothing outward."""
    if dry_run:
        return NullNotifier()
    if webhook_url:
        return WebhookNotifier(webhook_url)
    if console:
        return ConsoleNotifier()
    return NullNotifier()


def load_alerter(*, env=None, config_file=None, dry_run: bool = False,
                 console: bool = False) -> "Alerter":
    """Build an `Alerter` from the `[alerts]` section.

    Reads only `[alerts]`. It never sees `api_key`, exactly as `GateConfig` never does —
    one file, three readers, none able to reach another's secrets.

    Sending is OPT-IN. With no `webhook_url` configured this returns an alerter wired to a
    notifier that goes nowhere, so importing or scheduling this can never start posting to
    somebody's channel by default.
    """
    from ..config import ALERT_SECTION, ENV_PREFIX, ConfigChain

    chain = ConfigChain(env=env, config_file=config_file)

    def _get(name, env_suffix):
        return chain.get(name, section=ALERT_SECTION,
                         env_names=(ENV_PREFIX + env_suffix,)).value

    url = _get("webhook_url", "ALERT_WEBHOOK")
    policy = AlertPolicy()
    for name, env_suffix, cast, attr in (
        ("cooldown_s", "ALERT_COOLDOWN_S", float, "cooldown_s"),
        ("max_per_hour", "ALERT_MAX_PER_HOUR", int, "max_per_hour"),
        ("realert_on_improvement_pct", "ALERT_REALERT_IMPROVEMENT_PCT", float,
         "realert_on_improvement_pct"),
    ):
        raw = _get(name, env_suffix)
        if raw is None:
            continue
        try:
            setattr(policy, attr, cast(raw))
        except (TypeError, ValueError) as exc:
            raise ConfigError("bad puntersedge alert setting %s=%r: %s" % (name, raw, exc))

    raw_min = _get("min_edge_pct", "ALERT_MIN_EDGE_PCT")
    try:
        min_edge = float(raw_min) if raw_min is not None else 0.0
    except (TypeError, ValueError) as exc:
        raise ConfigError("bad puntersedge alert setting min_edge_pct=%r: %s"
                          % (raw_min, exc))

    return Alerter(
        notifier_from_config(url, dry_run=dry_run, console=console),
        policy,
        min_edge_pct=min_edge,
    )
