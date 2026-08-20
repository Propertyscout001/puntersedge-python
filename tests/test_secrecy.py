"""Regression tests for the seven verified API-key leak paths.

Every test here corresponds to a leak that was empirically demonstrated against this
package on 2026-08-20 with a sentinel key, not to a hypothetical. The `proven absent`
group at the bottom pins behaviour that was already clean, because "still clean" and
"nobody checked" are indistinguishable without a test.
"""
from __future__ import annotations

import pickle
import traceback

import pytest
import requests

from puntersedge import ConfigError, PuntersEdge, PuntersEdgeError
from puntersedge._secret import Secret

KEY = "pe_live_SENTINEL123"
ENV = {"PUNTERSEDGE_API_KEY": KEY}


def client(**kw):
    kw.setdefault("env", ENV)
    return PuntersEdge(**kw)


# ── Secret itself ────────────────────────────────────────────────────────────────────

def test_secret_hides_itself_in_every_stringification():
    s = Secret(KEY)
    assert KEY not in repr(s)
    assert KEY not in str(s)
    assert KEY not in "%s" % (s,)
    assert KEY not in f"{s}"          # __format__, not __str__ — the f-string path
    assert KEY not in "{}".format(s)
    assert s.reveal() == KEY


def test_secret_refuses_to_pickle():
    with pytest.raises(TypeError):
        pickle.dumps(Secret(KEY))


# ── L4: the client object ────────────────────────────────────────────────────────────

def test_no_plaintext_key_attribute():
    """vars() is what a debugger's variables pane and json.dumps(vars(obj)) show."""
    assert KEY not in str(vars(client()))


def test_repr_is_clean():
    assert KEY not in repr(client())


def test_client_refuses_to_pickle():
    with pytest.raises(TypeError, match="not picklable"):
        pickle.dumps(client())


def test_key_source_reports_origin_never_the_key():
    """Answers 'which key am I using?' without a prefix, last-4 or hash.

    A sha256 digest of a key was once emailed to two people labelled "YOUR API KEY" —
    a digest of a secret reads to the recipient as the secret.
    """
    pe = client()
    assert pe.key_source == "$PUNTERSEDGE_API_KEY"
    assert KEY not in pe.key_source


# ── L1: connection-failure traceback ─────────────────────────────────────────────────

def test_network_failure_traceback_does_not_carry_the_key():
    """`raise ... from None`. Without it, urllib3/http.client frames stay attached via
    __context__ and their locals hold the header dict and raw request bytes. A plain
    pytest run with NO flags printed the key twice before this fix."""
    pe = client(base_url="http://127.0.0.1:1/v1", retries=0)
    with pytest.raises(PuntersEdgeError) as ei:
        pe.sports()
    exc = ei.value
    # `from None` sets __suppress_context__; it does NOT clear __context__. Suppression is
    # what every traceback formatter honours, so that is the flag to assert on.
    assert exc.__suppress_context__ is True, "`from None` was dropped from the raise"
    te = traceback.TracebackException.from_exception(exc, capture_locals=True)
    assert KEY not in "".join(te.format())
    assert KEY not in "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


# ── L2 / L7: exceptions ──────────────────────────────────────────────────────────────

def test_http_error_does_not_retain_the_key_in_the_request(httpserver_401):
    url, _ = httpserver_401
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError) as ei:
        pe.sports()
    exc = ei.value
    assert KEY not in str(getattr(exc.response, "request", None) and exc.response.request.headers)


def test_http_error_does_not_pickle_the_key(httpserver_401):
    url, _ = httpserver_401
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError) as ei:
        pe.sports()
    assert KEY.encode() not in pickle.dumps(ei.value)


def test_echoed_key_is_redacted_from_the_message(httpserver_echo):
    """Production does not echo the key today, but a WAF page or an upstream proxy error
    that reflects the header would make this live with zero changes on our side."""
    url, _ = httpserver_echo
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError) as ei:
        pe.sports()
    assert KEY not in str(ei.value)
    assert "pe_***" in str(ei.value)


# ── L3: redirects ────────────────────────────────────────────────────────────────────

def test_redirect_is_refused_not_followed(httpserver_redirect):
    """requests strips only Authorization/Proxy-Authorization/Cookie across a cross-host
    redirect — never a custom auth header. A 302 was verified to deliver X-API-Key to the
    destination, and the SDK returned that destination's body as a normal result."""
    url, seen = httpserver_redirect
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError, match="does not redirect"):
        pe.sports()
    assert not seen, "the redirect target received a request (and with it, the key)"


# ── L5: base_url ─────────────────────────────────────────────────────────────────────

def test_plain_http_base_url_is_refused():
    with pytest.raises(ConfigError, match="unencrypted"):
        client(base_url="http://evil.example/v1")


def test_localhost_http_is_allowed_for_stubs():
    assert client(base_url="http://127.0.0.1:9/v1").base_url == "http://127.0.0.1:9/v1"


def test_nonsense_scheme_refused():
    with pytest.raises(ConfigError):
        client(base_url="file:///etc/passwd")


# ── L6: rotate_key ───────────────────────────────────────────────────────────────────

def test_rotate_key_updates_the_session(httpserver_rotate):
    """Before this fix the session kept the OLD, now-invalidated key, so the next call
    failed — and the natural reaction, print(pe.rotate_key()), wrote a live key to CI."""
    url, _ = httpserver_rotate
    pe = client(base_url=url)
    out = pe.rotate_key()
    assert out["api_key"] == "pe_live_ROTATED_NEW"
    assert pe._session.headers["X-API-Key"] == "pe_live_ROTATED_NEW"
    assert pe.key_source == "rotate_key() response"


def test_non_get_is_not_retried(httpserver_500_counter):
    """A retried POST can rotate a key twice; the second rotation invalidates the key the
    first one returned."""
    url, counter = httpserver_500_counter
    pe = client(base_url=url, retries=3)
    with pytest.raises(PuntersEdgeError):
        pe.rotate_key()
    assert counter["n"] == 1, "POST was retried %d times" % counter["n"]


# ── proven absent — pin these ────────────────────────────────────────────────────────

def test_key_never_appears_in_a_request_url(httpserver_401):
    url, seen = httpserver_401
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError):
        pe.odds("afl", markets="h2h")
    assert all(KEY not in p for p in seen["paths"])


def test_user_agent_matches_the_installed_version(httpserver_401):
    import puntersedge

    url, seen = httpserver_401
    pe = client(base_url=url)
    with pytest.raises(PuntersEdgeError):
        pe.sports()
    ua = seen["headers"][-1].get("User-Agent", "")
    assert ua.startswith("puntersedge-python/")
    assert "0.1.0" not in ua or puntersedge.__version__ == "0.1.0"
