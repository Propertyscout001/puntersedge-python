"""
PuntersEdge — official Python client for the Australian Sports Odds API.

Docs:     https://puntersedge.online/developers
API home: https://puntersedge.online/api-platform
Free key: https://puntersedge.online/api-platform#signup
"""
from __future__ import annotations

import time

import requests

from .exceptions import (
    AuthenticationError,
    NotFoundError,
    PuntersEdgeError,
    RateLimitError,
    ServerError,
)

__all__ = ["PuntersEdge"]

DEFAULT_BASE_URL = "https://api.puntersedge.online/v1"
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 2


class PuntersEdge:
    """Client for the PuntersEdge Australian Sports Odds API.

    Live bookmaker odds across 11 Australian books, racing next-to-go,
    best-odds comparison, and pre-computed arbitrage / value signals — all
    as clean JSON.

    Get a free API key (1,500 credits/mo, no credit card) at
    https://puntersedge.online/api-platform#signup

    Example
    -------
    >>> from puntersedge import PuntersEdge
    >>> pe = PuntersEdge("YOUR_API_KEY")
    >>> pe.sports()
    [{'key': 'nrl', 'title': 'NRL', ...}, ...]
    >>> pe.best_odds("nrl")[0]["selections"][0]["best_price"]
    1.96
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        session: "requests.Session | None" = None,
    ):
        if not api_key or not isinstance(api_key, str):
            raise ValueError(
                "An API key is required. Get a free one at "
                "https://puntersedge.online/api-platform#signup"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "puntersedge-python/0.1.0",
            }
        )

    # ── transport ────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.request(
                    method, url, params=params, json=json, timeout=self.timeout
                )
            except requests.RequestException as exc:  # network / timeout
                last_exc = PuntersEdgeError(f"Request to {url} failed: {exc}")
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_exc
            if resp.status_code >= 500 and attempt < self.retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            return self._handle(resp)
        raise last_exc  # pragma: no cover

    @staticmethod
    def _handle(resp: "requests.Response"):
        if 200 <= resp.status_code < 300:
            if not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text
        try:
            detail = resp.json().get("detail") or resp.json().get("error") or resp.text
        except ValueError:
            detail = resp.text or resp.reason
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

    def odds(self, sport_key: str, markets=None, bookmakers=None, odds_format=None):
        """Odds for a sport. ``markets`` e.g. "h2h", "spreads", "totals"."""
        return self._get(
            f"/sports/{sport_key}/odds",
            markets=markets,
            bookmakers=bookmakers,
            oddsFormat=odds_format,
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

    # ── arbitrage & value ────────────────────────────────────────────────
    def arb_sports(self, sport_key=None, min_profit_pct=None):
        """Scan sports markets for arbitrage + best-odds overlays."""
        return self._get("/arb/sports", sport_key=sport_key, min_profit_pct=min_profit_pct)

    def arb_racing(self, categories=None, min_edge_pct=None, verify=None):
        """Racing back/lay arb: fixed-odds back vs Betfair exchange lay."""
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
        """Rotate your API key; the old key is invalidated immediately."""
        return self._request("POST", "/keys/rotate")

    def health(self):
        """Connector health for monitoring."""
        return self._get("/health")

    def uptime(self):
        """Public uptime stats."""
        return self._get("/uptime")

    def __repr__(self):
        return f"PuntersEdge(base_url={self.base_url!r})"
