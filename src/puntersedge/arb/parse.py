"""Turn raw `/v1/arb/*` payloads into `Opportunity` objects.

Written against the live response shapes, not from memory. The rule the operator's estate
learned the hard way: enumerate endpoints and fields from `api.puntersedge.online/openapi.json`
before writing any example — a documented endpoint that does not exist shipped a 404-ing curl
onto five published comparison pages.

WHY `is_arb` FROM THE SERVER IS LOAD-BEARING
--------------------------------------------
`/v1/arb/sports` sets `arb_pct` to 0.0 when the priced selections do not cover the full
outcome space, even when the best prices do sum below 1.0. That check needs `max_outcomes` —
the fullest outcome space any single bookmaker listed for the market — and `max_outcomes` is
NOT in the response. So a client cannot re-derive it, and a client that ignores `is_arb` and
recomputes from `best_price` alone will resurrect the exact phantom the server is suppressing:
a drawless reading of a three-way soccer market, where Juventus 2.65 + Inter 2.55 sums to 0.77
and reads as a 23% arb, but the draw loses both legs. That was 13 of 25 bad alerts before it
was fixed server-side on 2026-08-15.

The parser therefore preserves `is_arb` verbatim and `gates.refusal_reasons` refuses on it.
Do not "improve" this by recomputing locally.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .models import ArbKind, Leg, Opportunity


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """Accept a bare list or any of the envelope keys the API family uses."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "arbs", "opportunities", "data", "items"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
    return []


def _event_name(row: Dict[str, Any]) -> str:
    home, away = row.get("home_team"), row.get("away_team")
    if home and away:
        return "%s v %s" % (home, away)
    return str(home or away or row.get("event_id") or "event")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def from_sports_payload(
    payload: Any, ages: Optional[Dict[str, float]] = None
) -> List[Opportunity]:
    """Parse `client.arb_sports(...)` output.

    `ages` optionally maps `event_id` -> age in seconds, so a scanner can enrich from
    `/v1/sports/{sport_key}/odds` (which exposes `data_age_seconds`). Without it every leg
    carries an unknown age and the default gate config refuses the lot with `unknown_age`.
    That is the intended, loud behaviour — see `gates.UnknownAge`.
    """
    out: List[Opportunity] = []
    for row in _rows(payload):
        selections = row.get("selections") or []
        if not isinstance(selections, list) or len(selections) < 2:
            continue
        event_id = str(row.get("event_id") or "")
        age = (ages or {}).get(event_id)
        legs = [
            Leg(
                book=str(sel.get("best_bookmaker") or ""),
                selection=str(sel.get("name") or ""),
                odds=_f(sel.get("best_price")),
                quote_age_s=age,
            )
            for sel in selections
            if isinstance(sel, dict)
        ]
        if len(legs) < 2:
            continue
        out.append(
            Opportunity(
                kind=ArbKind.SPORTS_BACK_BACK,
                event_name=_event_name(row),
                sport=str(row.get("sport_key") or ""),
                legs=legs,
                edge_pct=_f(row.get("arb_pct")),
                source="/v1/arb/sports",
                raw=row,
            )
        )
    return out


def from_lines_payload(
    payload: Any, ages: Optional[Dict[str, float]] = None
) -> List[Opportunity]:
    """Parse `client.arb_lines(...)` output.

    One `Opportunity` per entry in a row's `opportunities` list — a row is an event/market
    pair and can carry more than one.

    `is_middle` is preserved in `raw` but deliberately does NOT relax any gate. A middle is a
    position where both sides can win; that is upside, not a reason to accept a staler price
    or a larger implausible edge.
    """
    out: List[Opportunity] = []
    for row in _rows(payload):
        event_id = str(row.get("event_id") or "")
        age = (ages or {}).get(event_id)
        market_type = str(row.get("market_type") or "")
        for opp in row.get("opportunities") or []:
            if not isinstance(opp, dict):
                continue
            legs = []
            for side_key in ("side_a", "side_b"):
                side = opp.get(side_key)
                if not isinstance(side, dict):
                    legs = []
                    break
                point = side.get("point")
                name = str(side.get("name") or "")
                legs.append(
                    Leg(
                        book=str(side.get("bookmaker") or ""),
                        selection="%s %s" % (name, point) if point is not None else name,
                        odds=_f(side.get("price")),
                        quote_age_s=age,
                    )
                )
            if len(legs) != 2:
                continue
            out.append(
                Opportunity(
                    kind=ArbKind.LINES_BACK_BACK,
                    event_name="%s (%s)" % (_event_name(row), market_type)
                    if market_type
                    else _event_name(row),
                    sport=str(row.get("sport_key") or ""),
                    legs=legs,
                    edge_pct=_f(opp.get("arb_pct")),
                    source="/v1/arb/lines",
                    raw=dict(opp, market_type=market_type, event_id=event_id),
                )
            )
    return out
