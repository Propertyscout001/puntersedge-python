"""
PuntersEdge — official Python client for the Australian Sports Odds API.

Docs:     https://puntersedge.online/developers
API home: https://puntersedge.online/api-platform
Free key: https://puntersedge.online/api-platform#signup
"""
from __future__ import annotations

import time
from typing import Mapping, Optional, Union
from urllib.parse import urlsplit

import requests

from ._secret import Secret
from .config import ENV_PREFIX, ConfigChain, PathLike, resolve_api_key
from .exceptions import (
    AuthenticationError,
    ConfigError,
    NotFoundError,
    PuntersEdgeError,
    RateLimitError,
    ServerError,
)

__all__ = ["PuntersEdge"]

DEFAULT_BASE_URL = "https://api.puntersedge.online/v1"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 2

_API_KEY_HEADER = "X-API-Key"
# Hosts where plain http is legitimate, for local development against a stub.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def _client_version() -> str:
    """The installed version, so the User-Agent cannot drift from the release.

    Read at runtime rather than hardcoded: this shipped as a literal "0.1.0" inside a
    0.2.0 package, which made every client misreport itself and would have made
    server-side adoption telemetry for the release simply wrong. `from . import
    __version__` would be a circular import, hence importlib.metadata.
    """
    try:
        from importlib.metadata import version

        return version("puntersedge")
    except Exception:  # not installed (running from a source tree), or 3.8 edge cases
        return "0.0.0+unknown"


def _validate_base_url(url: str) -> str:
    """Require https, except against a local stub.

    `base_url` was previously taken verbatim. `http://...` was verified to put the API key
    on the wire in cleartext, and with HTTP_PROXY set (requests honours the environment by
    default) a local recorder acting as the proxy received the whole header. Reachable by a
    typo, a pasted staging URL, or $PUNTERSEDGE_BASE_URL.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ConfigError(
            "base_url must be an http(s) URL, got %r." % (url,)
        )
    host = (parts.hostname or "").lower()
    if parts.scheme == "http" and host not in _LOCAL_HOSTS:
        raise ConfigError(
            "base_url %r uses plain http, which would send your API key unencrypted. "
            "Use https:// (plain http is allowed only against localhost)." % (url,)
        )
    return url.rstrip("/")


def _coerce(fn, raw: Optional[str], default, name: str):
    """Coerce a config/env string, naming the setting when it fails.

    A bare `ValueError: could not convert string to float: 'abc'` does not tell you which
    setting, or which of two possible sources, was wrong.
    """
    if raw is None:
        return default
    try:
        return fn(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError("bad puntersedge setting %s=%r: %s" % (name, raw, exc))


def _redact(text: object, secret: Secret) -> str:
    """Replace the key with a marker anywhere it appears in server-supplied text."""
    out = text if isinstance(text, str) else str(text)
    raw = secret.reveal()  # audited reveal 2 of 2
    return out.replace(raw, "pe_***") if raw else out


class PuntersEdge:
    """Client for the PuntersEdge Australian Sports Odds API.

    Live bookmaker odds across 11 Australian books, racing next-to-go,
    best-odds comparison, and pre-computed arbitrage / value signals — all
    as clean JSON.

    Get a free API key (1,500 credits/mo, no credit card) at
    https://puntersedge.online/api-platform#signup

    This package never holds bookmaker credentials, never places bets, and never operates
    a betting account. It reads odds and computes sizing; you place every bet yourself in
    your own session.

    Example
    -------
    >>> from puntersedge import PuntersEdge
    >>> pe = PuntersEdge()               # key from $PUNTERSEDGE_API_KEY or your config file
    >>> pe.sports()
    [{'key': 'nrl', 'title': 'NRL', ...}, ...]
    >>> pe.best_odds("nrl")[0]["selections"][0]["best_price"]
    1.96

    Passing the key inline still works — ``PuntersEdge("YOUR_API_KEY")`` — but prefer the
    no-argument form: the scripts people write with this library are the ones they commit.

    Key lookup, in order (per value, so a file's ``base_url`` survives an environment
    ``api_key``):

    1. the ``api_key=`` argument
    2. ``$PUNTERSEDGE_API_KEY``
    3. ``$PUNTERSEDGE_CONFIG_FILE``, if set — then that file only
    4. ``~/.config/puntersedge/config`` (``%APPDATA%\\puntersedge\\config`` on Windows)

    ``client.key_source`` reports which of those supplied the key, without printing any
    part of it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        session: Optional["requests.Session"] = None,
        *,
        config_file: Optional[PathLike] = None,
        env: Optional[Mapping[str, str]] = None,
    ):
        chain = ConfigChain(env=env, config_file=config_file)
        # Resolved at CONSTRUCTION, so a missing key points the traceback at PuntersEdge()
        # rather than at whichever call happened to be the first to need it.
        key = resolve_api_key(api_key, env=env, config_file=config_file)
        self._api_key = Secret(key)
        # Where it came from, for "which key am I using?". Deliberately the SOURCE and never
        # a prefix, last-4, or hash of the key itself: a sha256 digest of a key was once
        # emailed to two people labelled "YOUR API KEY", because a digest of a secret reads
        # to the recipient as the secret.
        self.key_source = chain.get(
            "api_key", explicit=api_key, env_names=(ENV_PREFIX + "API_KEY",)
        ).source

        # Per-value resolution: base_url from the file must survive an API key supplied via
        # the environment, or a CI run points at production while the file said staging.
        resolved_url = chain.get(
            "base_url", explicit=base_url, env_names=(ENV_PREFIX + "BASE_URL",)
        ).value
        self.base_url = _validate_base_url(resolved_url or DEFAULT_BASE_URL)

        raw_timeout = chain.get(
            "timeout", explicit=None if timeout is None else str(timeout),
            env_names=(ENV_PREFIX + "TIMEOUT",),
        ).value
        raw_retries = chain.get(
            "retries", explicit=None if retries is None else str(retries),
            env_names=(ENV_PREFIX + "RETRIES",),
        ).value
        self.timeout = _coerce(float, raw_timeout, DEFAULT_TIMEOUT, "timeout")
        self.retries = max(0, _coerce(int, raw_retries, DEFAULT_RETRIES, "retries"))

        self._session = session or requests.Session()
        # The key lives in exactly ONE mutable place — this header. There is deliberately
        # no `self.api_key` attribute: `vars(client)` was verified to expose it, which is
        # what a debugger's variables pane and `json.dumps(vars(obj))` both show.
        self._session.headers.update(
            {
                _API_KEY_HEADER: self._api_key.reveal(),  # audited reveal 1 of 2
                "Accept": "application/json",
                "User-Agent": "puntersedge-python/%s" % _client_version(),
            }
        )

    # A constructed client carries a live key. Pickling one sends it to a multiprocessing
    # worker, a joblib cache, or Streamlit session state — all places nobody audited.
    def __getstate__(self):
        raise TypeError(
            "PuntersEdge clients are not picklable because they hold a live API key. "
            "Pass the key to each process and construct a client there."
        )

    # ── transport ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # Only GET is retried. A retried POST can rotate a key twice, and the second
        # rotation invalidates the key the first one returned.
        attempts = self.retries if method.upper() == "GET" else 0
        last_exc = None
        for attempt in range(attempts + 1):
            try:
                resp = self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    timeout=self.timeout,
                    # requests strips only Authorization/Proxy-Authorization/Cookie across
                    # a cross-host redirect — never a custom auth header. A 302 to another
                    # hostname was verified to deliver X-API-Key to the destination, and
                    # the SDK returned that destination's body as a normal result. A JSON
                    # API has no legitimate reason to 3xx, so refuse to follow at all.
                    allow_redirects=False,
                )
            except requests.RequestException as exc:  # network / timeout
                last_exc = PuntersEdgeError(f"Request to {url} failed: {exc}")
                if attempt < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                # `from None` drops __context__. Without it the urllib3/http.client frames
                # stay attached, and their locals hold the header dict and the raw request
                # bytes — a plain `pytest` run with NO flags was verified to print the key
                # twice on a connection failure, and --showlocals nine times. Nothing
                # diagnostic is lost: str(exc) already carries the url and the cause.
                raise last_exc from None
            if 300 <= resp.status_code < 400:
                raise PuntersEdgeError(
                    "%s returned a redirect to %r. The PuntersEdge API does not redirect; "
                    "following it would send your API key to another host. Check base_url."
                    % (url, resp.headers.get("Location", "?")),
                    resp.status_code,
                    None,
                )
            if resp.status_code >= 500 and attempt < attempts:
                time.sleep(0.5 * (attempt + 1))
                continue
            return self._handle(resp)
        raise last_exc from None  # pragma: no cover

    def _handle(self, resp: "requests.Response"):
        """Turn a response into data or an exception.

        An instance method rather than a staticmethod, so it can redact. That is the only
        reason it changed.
        """
        if 200 <= resp.status_code < 300:
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text
        # Scrub before the exception is constructed. `exc.response.request.headers` was
        # verified to hold the key, and `pickle.dumps(exc)` to contain it in plaintext —
        # while str(exc) and repr(exc) looked clean, which is what made it easy to miss.
        try:
            resp.request.headers.pop(_API_KEY_HEADER, None)
        except AttributeError:
            pass
        try:
            detail = resp.json().get("detail") or resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text or resp.reason
        # Defensive redaction of an echoed key. Production does not echo it today, but a
        # WAF block page, an upstream proxy error, or a future validation error that
        # reflects the header would make this live with zero changes on our side.
        detail = _redact(detail, self._api_key)
        msg = f"{resp.status_code} {resp.reason}: {detail}"
        if resp.status_code in (401, 403):
            raise AuthenticationError(msg, resp.status_code, resp)
        if resp.status_code == 404:
            raise NotFoundError(msg, resp.status_code, resp)
        if resp.status_code == 429:
            raise RateLimitError(msg, resp.status_code, resp)
        if resp.status_code >= 500:
            raise ServerError(msg, resp.status_code, resp)
        raise PuntersEdgeError(msg, resp.status_code, resp)

    def _get(self, path: str, **params):
        return self._request("GET", path, params=params)

    # ── sports & odds ────────────────────────────────────────────────────
    def sports(self):
        """List active sports with their keys, titles and groups."""
        return self._get("/sports")

    def odds(self, sport_key: str, markets=None, bookmakers=None, odds_format=None,
             max_age_minutes=None):
        """Odds for a sport. ``markets`` e.g. "h2h", "spreads", "totals".

        ``max_age_minutes`` excludes bookmaker markets older than that. The API defaults
        it to 360 and caps it at 1440 — and the default is not harmless: a book older than
        six hours is dropped from the payload entirely, so a caller reading per-bookmaker
        ages sees "no age" rather than "an old age". Pass it explicitly when that
        distinction matters.
        """
        return self._get(
            f"/sports/{sport_key}/odds",
            markets=markets,
            bookmakers=bookmakers,
            oddsFormat=odds_format,
            maxAgeMinutes=max_age_minutes,
        )

    def odds_history(self, sport_key: str, date_from=None, date_to=None):
        """Historical odds snapshots over a date range (Pro+ tier)."""
        return self._get(f"/sports/{sport_key}/odds/history", date_from=date_from, date_to=date_to)

    def odds_movements(self, sport_key: str):
        """Detected price movements from historical snapshots (Pro+ tier)."""
        return self._get(f"/sports/{sport_key}/odds/movements")

    def best_odds(self, sport_key: str):
        """Best available price per selection across all books, with arb flags."""
        return self._get(f"/best-odds/{sport_key}")

    def popular_markets(self):
        """Currently popular markets across covered sports."""
        return self._get("/markets/popular")

    # ── racing ───────────────────────────────────────────────────────────
    def racing_next_to_go(self, categories=None):
        """Next races to jump with runners and prices.

        ``categories`` e.g. "horse", "greyhound", "harness" (comma-joined).
        """
        return self._get("/racing/next-to-go", categories=categories)

    def racing_events(self, hours_ahead=None, categories=None):
        """Upcoming races within an ``hours_ahead`` window."""
        return self._get("/racing/events", hours_ahead=hours_ahead, categories=categories)

    def racing_best_odds(self, categories=None, num_races=None, bookmakers=None, country=None):
        """Best win, place and tote price per runner across the AU books. 3 credits.

        ``market_percentage`` under 100 means the best prices across books beat the field —
        cross-book value without needing an exchange. This is the endpoint to use instead of
        :meth:`arb_racing`, which is withheld from customer keys.
        """
        return self._get(
            "/racing/best-odds", categories=categories, num_races=num_races,
            bookmakers=bookmakers, country=country
        )

    # ── arbitrage & value ────────────────────────────────────────────────
    def arb_sports(self, sport_key=None, min_profit_pct=None):
        """Scan sports markets for arbitrage + best-odds overlays."""
        return self._get("/arb/sports", sport_key=sport_key, min_profit_pct=min_profit_pct)

    def arb_racing(self, categories=None, min_edge_pct=None, verify=None):
        """WITHHELD — raises ``PuntersEdgeError`` (HTTP 410) on every customer key.

        Racing back/lay arb needs the Betfair exchange lay side, which is withheld pending a
        Betfair Exchange data licence. This is not a temporary outage and retrying will not
        help. Use :meth:`racing_best_odds` instead: ``market_percentage`` under 100 is
        cross-book value that needs no exchange.

        Kept rather than removed because it ships in a published release and deleting it
        would break an import.
        """
        return self._get(
            "/arb/racing", categories=categories, min_edge_pct=min_edge_pct, verify=verify
        )

    def arb_lines(self, sport_key=None, min_profit_pct=None):
        """Line-matched arbitrage on spreads/totals."""
        return self._get("/arb/lines", sport_key=sport_key, min_profit_pct=min_profit_pct)

    def arb_best_prices(self, sport_key=None):
        """Best price per selection plus full per-book comparison."""
        return self._get("/arb/best-prices", sport_key=sport_key)

    def value_promos(self, book=None, type=None, min_ev=None):
        """Daily promo value board ranked by EV per $1."""
        return self._get("/value/promos", book=book, type=type, min_ev=min_ev)

    # ── account & ops ────────────────────────────────────────────────────
    def usage(self):
        """Credits used and remaining this month for your key."""
        return self._get("/usage")

    def usage_analytics(self, days=None):
        """Usage broken down by endpoint over the last N days."""
        return self._get("/usage/analytics", days=days)

    def data_quality_summary(self):
        """Connector freshness, canonical mappings and recent audit status."""
        return self._get("/data-quality/summary")

    def key_info(self):
        """Metadata about the current API key (plan, limits)."""
        return self._get("/keys/info")

    def rotate_key(self):
        """Rotate your API key; the old key is invalidated immediately.

        ⚠️ The returned dict contains your NEW key in plaintext. Do not print it, log it,
        or return it from a web handler — store it and move on.

        This client updates itself in place, so calls after a rotation keep working.
        Previously it did not: the session still carried the old, now-invalidated key, so
        the next call failed and the natural reaction — ``print(pe.rotate_key())`` — wrote
        a live freshly-minted key to stdout and into CI logs.
        """
        result = self._request("POST", "/keys/rotate")
        new_key = None
        if isinstance(result, dict):
            new_key = result.get("api_key") or result.get("key")
        if isinstance(new_key, str) and new_key.strip():
            self._api_key = Secret(new_key)
            self._session.headers[_API_KEY_HEADER] = new_key
            self.key_source = "rotate_key() response"
        return result

    def health(self):
        """Connector health for monitoring."""
        return self._get("/health")

    def uptime(self):
        """Public uptime stats."""
        return self._get("/uptime")

    def __repr__(self):
        return f"PuntersEdge(base_url={self.base_url!r})"
