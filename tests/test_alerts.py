"""Alert throttling, delivery isolation, and secret handling.

The two headline tests are `test_identity_ignores_everything_that_moves` and
`test_a_failed_send_consumes_no_quota`. Both correspond to production incidents where the
policy was correct and correctly tested, and the mechanism underneath it silently did not
engage.
"""
from __future__ import annotations

import pytest

from puntersedge.arb import ArbKind, Leg, Opportunity
from puntersedge.arb.alerts import (
    AlertDeliveryError,
    AlertPolicy,
    Alerter,
    ConsoleNotifier,
    NullNotifier,
    WebhookNotifier,
    alert_identity,
    load_alerter,
    render,
)
from puntersedge.arb.sizing import size
from puntersedge.exceptions import ConfigError

HOOK = "https://discord.com/api/webhooks/123/SECRETTOKEN"


def opp(edge=2.4, odds=(2.10, 2.10), books=("sportsbet", "tab"), age=5.0, event="Lions v Magpies"):
    return Opportunity(
        kind=ArbKind.SPORTS_BACK_BACK, event_name=event, sport="afl",
        legs=[Leg(books[i], "sel%d" % i, o, quote_age_s=age) for i, o in enumerate(odds)],
        edge_pct=edge, raw={"is_arb": True},
    )


# ── RULE 1: identity must not move ───────────────────────────────────────────────────

def test_identity_ignores_everything_that_moves():
    """The flood: a live counter in the compared text made "has this changed?" always true,
    so a 12-hourly throttle never engaged once in three days."""
    base = alert_identity(opp())
    assert alert_identity(opp(edge=9.9)) == base, "edge is in the identity"
    assert alert_identity(opp(odds=(2.50, 2.50))) == base, "odds are in the identity"
    assert alert_identity(opp(age=9999.0)) == base, "price age is in the identity"


def test_identity_distinguishes_genuinely_different_arbs():
    assert alert_identity(opp(event="Blues v Saints")) != alert_identity(opp())
    assert alert_identity(opp(books=("neds", "tab"))) != alert_identity(opp())


def test_identity_is_stable_across_leg_order():
    a = opp(books=("sportsbet", "tab"))
    b = opp(books=("sportsbet", "tab"))
    b.legs.reverse()
    b.legs[0].selection, b.legs[1].selection = b.legs[0].selection, b.legs[1].selection
    assert alert_identity(a) == alert_identity(b)


def test_identity_is_case_and_space_insensitive():
    assert alert_identity(opp(books=("SportsBet", " TAB "))) == alert_identity(opp())


def test_repeat_alerts_are_suppressed_by_cooldown():
    n = NullNotifier()
    a = Alerter(n, AlertPolicy(cooldown_s=3600))
    assert a.notify([opp()], now=1000.0).sent == 1
    r = a.notify([opp(edge=2.5)], now=1100.0)     # edge ticked; still the same arb
    assert r.sent == 0
    assert r.suppressed == {"cooldown": 1}
    assert len(n.sent) == 1


def test_cooldown_expiry_allows_a_resend():
    a = Alerter(NullNotifier(), AlertPolicy(cooldown_s=600))
    a.notify([opp()], now=1000.0)
    assert a.notify([opp()], now=1700.0).sent == 1


def test_material_improvement_can_beat_the_cooldown():
    a = Alerter(NullNotifier(), AlertPolicy(cooldown_s=3600,
                                            realert_on_improvement_pct=2.0))
    a.notify([opp(edge=2.0)], now=1000.0)
    assert a.notify([opp(edge=2.5)], now=1100.0).sent == 0   # +0.5, not material
    assert a.notify([opp(edge=5.0)], now=1200.0).sent == 1   # +3.0, material


def test_improvement_threshold_is_off_by_default():
    a = Alerter(NullNotifier(), AlertPolicy(cooldown_s=3600))
    a.notify([opp(edge=2.0)], now=1000.0)
    assert a.notify([opp(edge=99.0)], now=1100.0).sent == 0


# ── RULE 2: peek then record ─────────────────────────────────────────────────────────

class Broken(ConsoleNotifier):
    name = "broken"

    def send(self, text):
        raise AlertDeliveryError("webhook to example.invalid returned 500")


def test_a_failed_send_consumes_no_quota():
    """The self-perpetuating lockout: a rejected call recorded itself, so every retry
    pushed its own unblock time further out and the window could never be waited out."""
    policy = AlertPolicy(max_per_hour=2)
    a = Alerter(Broken(), policy)
    for _ in range(5):
        a.notify([opp()], now=1000.0)
    assert policy._sent_at == [], "failed sends burned quota"
    # ...and a working notifier still has its full allowance
    ok = Alerter(NullNotifier(), policy)
    assert ok.notify([opp()], now=1000.0).sent == 1


def test_a_failed_send_does_not_start_a_cooldown():
    policy = AlertPolicy(cooldown_s=3600)
    Alerter(Broken(), policy).notify([opp()], now=1000.0)
    assert policy.allows(alert_identity(opp()), now=1001.0) is None


def test_a_suppressed_alert_consumes_no_quota():
    policy = AlertPolicy(cooldown_s=3600, max_per_hour=10)
    a = Alerter(NullNotifier(), policy)
    a.notify([opp()], now=1000.0)
    for _ in range(5):
        a.notify([opp()], now=1010.0)      # all suppressed by cooldown
    assert len(policy._sent_at) == 1


def test_rate_cap_blocks_and_then_releases():
    policy = AlertPolicy(cooldown_s=0, max_per_hour=2)
    a = Alerter(NullNotifier(), policy)
    assert a.notify([opp(event="a"), opp(event="b"), opp(event="c")], now=1000.0).sent == 2
    assert a.notify([opp(event="d")], now=1100.0).suppressed == {"rate_capped": 1}
    # an hour later the window has rolled
    assert a.notify([opp(event="d")], now=1000.0 + 3601).sent == 1


def test_allows_returns_a_reason_not_a_bool():
    """So a quiet alerter can say WHY it is quiet."""
    policy = AlertPolicy(cooldown_s=3600)
    ident = alert_identity(opp())
    assert policy.allows(ident, now=1000.0) is None
    policy.record_sent(ident, now=1000.0)
    assert policy.allows(ident, now=1001.0) == "cooldown"


# ── delivery isolation ───────────────────────────────────────────────────────────────

def test_a_delivery_failure_does_not_stop_later_alerts():
    class Flaky(ConsoleNotifier):
        name = "flaky"

        def __init__(self):
            self.n = 0
            self.ok = []

        def send(self, text):
            self.n += 1
            if self.n == 1:
                raise AlertDeliveryError("webhook to example.invalid returned 502")
            self.ok.append(text)
            return True

    f = Flaky()
    r = Alerter(f, AlertPolicy(cooldown_s=0)).notify(
        [opp(event="a"), opp(event="b")], now=1000.0)
    assert r.sent == 1
    assert len(r.errors) == 1
    assert len(f.ok) == 1


def test_a_sizing_failure_still_sends_the_alert():
    def boom(_):
        raise ZeroDivisionError("bad")

    r = Alerter(NullNotifier(), AlertPolicy()).notify([opp()], now=1000.0, sizer=boom)
    assert r.sent == 1
    assert any("ZeroDivisionError" in e for e in r.errors)


def test_min_edge_filters_before_the_policy_is_touched():
    policy = AlertPolicy()
    r = Alerter(NullNotifier(), policy, min_edge_pct=5.0).notify([opp(edge=1.0)], now=1000.0)
    assert r.suppressed == {"below_min_edge": 1}
    assert policy._sent_at == []


# ── secrets ──────────────────────────────────────────────────────────────────────────

def test_webhook_url_is_never_in_the_repr():
    w = WebhookNotifier(HOOK)
    assert "SECRETTOKEN" not in repr(w)
    assert "discord.com" in repr(w)


def test_webhook_url_is_never_in_a_delivery_error():
    class Dead:
        @staticmethod
        def post(*a, **kw):
            raise OSError("connection refused")

    w = WebhookNotifier(HOOK, session=Dead())
    with pytest.raises(AlertDeliveryError) as ei:
        w.send("hi")
    assert "SECRETTOKEN" not in str(ei.value)
    assert "discord.com" in str(ei.value)


def test_webhook_error_on_bad_status_carries_no_url():
    class Resp:
        status_code = 403

    class S:
        @staticmethod
        def post(*a, **kw):
            return Resp()

    with pytest.raises(AlertDeliveryError) as ei:
        WebhookNotifier(HOOK, session=S()).send("hi")
    assert "SECRETTOKEN" not in str(ei.value)
    assert "403" in str(ei.value)


def test_plain_http_webhook_is_refused():
    with pytest.raises(ConfigError, match="https"):
        WebhookNotifier("http://discord.com/api/webhooks/1/x")


def test_rendered_alert_contains_no_secret_and_says_who_places_the_bet():
    text = render(opp(), size(opp(), 100, step=1.0))
    assert "SECRETTOKEN" not in text
    assert "place these yourself" in text


# ── config ───────────────────────────────────────────────────────────────────────────

def test_alerts_are_opt_in_and_default_to_going_nowhere(tmp_path):
    p = tmp_path / "config"
    p.write_text("[puntersedge]\napi_key = k\n", encoding="utf-8")
    p.chmod(0o600)
    a = load_alerter(env={}, config_file=str(p))
    assert a.notifier.name == "null", "alerts delivered somewhere without being configured"


def test_alerts_section_is_read(tmp_path):
    p = tmp_path / "config"
    p.write_text(
        "[puntersedge]\napi_key = k\n\n[alerts]\nwebhook_url = %s\n"
        "min_edge_pct = 1.5\ncooldown_s = 900\nmax_per_hour = 5\n" % HOOK,
        encoding="utf-8",
    )
    p.chmod(0o600)
    a = load_alerter(env={}, config_file=str(p))
    assert a.notifier.name == "webhook"
    assert a.min_edge_pct == 1.5
    assert a.policy.cooldown_s == 900.0
    assert a.policy.max_per_hour == 5


def test_alerter_never_reads_the_api_key(tmp_path):
    p = tmp_path / "config"
    p.write_text("[puntersedge]\napi_key = pe_live_NOPE\n\n[alerts]\ncooldown_s = 60\n", encoding="utf-8")
    p.chmod(0o600)
    a = load_alerter(env={}, config_file=str(p))
    assert "pe_live_NOPE" not in repr(a.__dict__)


def test_a_bookmaker_section_is_still_refused_with_alerts_allowed(tmp_path):
    """Widening the allowlist to three sections must not widen the boundary."""
    from puntersedge.config import load_config_file

    p = tmp_path / "config"
    p.write_text("[alerts]\nwebhook_url = %s\n\n[sportsbet]\nusername = bob\n" % HOOK, encoding="utf-8")
    p.chmod(0o600)
    with pytest.raises(ConfigError, match="never stores bookmaker logins"):
        load_config_file(p, required=True)


def test_a_login_field_inside_alerts_is_refused(tmp_path):
    from puntersedge.config import load_config_file

    p = tmp_path / "config"
    p.write_text("[alerts]\nwebhook_url = %s\npassword = hunter2\n" % HOOK, encoding="utf-8")
    p.chmod(0o600)
    with pytest.raises(ConfigError, match="never stores bookmaker logins"):
        load_config_file(p, required=True)


def test_bad_alert_setting_names_the_setting(tmp_path):
    p = tmp_path / "config"
    p.write_text("[alerts]\ncooldown_s = soon\n", encoding="utf-8")
    p.chmod(0o600)
    with pytest.raises(ConfigError, match="cooldown_s"):
        load_alerter(env={}, config_file=str(p))


# ── persistence ──────────────────────────────────────────────────────────────────────

def test_state_survives_a_restart():
    """Without this, a crash loop becomes a flood."""
    p1 = AlertPolicy(cooldown_s=3600)
    Alerter(NullNotifier(), p1).notify([opp()], now=1000.0)
    p2 = AlertPolicy(cooldown_s=3600)
    p2.load_dict(p1.to_dict())
    assert Alerter(NullNotifier(), p2).notify([opp()], now=1100.0).sent == 0


def test_corrupt_persisted_state_is_skipped_not_fatal():
    p = AlertPolicy()
    p.load_dict({"seen": {"x": {"last_sent_at": "nope"}}, "sent_at": ["bad", 5.0]})
    assert p._seen == {}
    assert p._sent_at == [5.0]
