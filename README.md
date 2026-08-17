# PuntersEdge — Australian Sports Odds API (Python client)

Official Python client for the [**PuntersEdge Australian Sports Odds API**](https://puntersedge.online/api-platform) — live bookmaker odds across 11 Australian books, racing next-to-go, best-odds comparison, and pre-computed **arbitrage / value** signals, all as clean JSON.

> Get a **free API key** (1,500 credits/month, no credit card) → **[puntersedge.online/api-platform](https://puntersedge.online/api-platform#signup)**

[![PyPI](https://img.shields.io/pypi/v/puntersedge.svg)](https://pypi.org/project/puntersedge/)
[![Python](https://img.shields.io/pypi/pyversions/puntersedge.svg)](https://pypi.org/project/puntersedge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Why this API

Most "sports odds API" products have thin Australian coverage. PuntersEdge is **Australian-first**: Sportsbet, TAB, Neds, Ladbrokes, Unibet, Betfair, PointsBet, Betr, BetRight, PlayUp and TABtouch, across AFL, NRL, NBA, tennis, EPL, cricket, plus **horse / greyhound / harness racing**.

- 🟢 **Live bookmaker odds** — one REST endpoint, 11 AU books
- 🏇 **Racing next-to-go** — runners + prices for horse, greyhound, harness
- ⚖️ **Best-odds comparison** — best price per selection across every book
- 🎯 **Arbitrage, pre-computed** — surebets, spreads/totals line arbs, and racing back-vs-Betfair-lay edges with stakes + guaranteed profit already calculated
- 📊 **Value & promo boards** — daily plays ranked by EV per $1
- 🔌 **Predictable JSON** — simple `X-API-Key` auth

Full docs: **[puntersedge.online/developers](https://puntersedge.online/developers)** · Pricing: **[puntersedge.online/api/pricing](https://puntersedge.online/api/pricing)**

## Install

```bash
pip install puntersedge
```

## Quickstart

```python
from puntersedge import PuntersEdge

pe = PuntersEdge("YOUR_API_KEY")  # https://puntersedge.online/api-platform#signup

# List active sports
for sport in pe.sports():
    print(sport["key"], "-", sport["title"])

# Head-to-head odds for the NRL
nrl = pe.odds("nrl", markets="h2h")

# Best available price per selection across all books
for event in pe.best_odds("nrl"):
    for sel in event["selections"]:
        print(sel["name"], sel["best_price"], "@", sel["best_bookmaker"])

# Next races to jump (horse + greyhound)
races = pe.racing_next_to_go(categories="horse,greyhound")
```

## Arbitrage scanner in 5 lines

```python
from puntersedge import PuntersEdge

pe = PuntersEdge("YOUR_API_KEY")
for arb in pe.arb_sports(min_profit_pct=0):
    if arb.get("is_arb"):
        print(f"{arb['home_team']} v {arb['away_team']}: {arb['arb_pct']}%")
        for leg in arb["optimal_stakes"]:
            print(f"   stake ${leg['stake']} on {leg['name']} @ {leg['bookmaker']}")
```

Racing back/lay arb against the Betfair exchange:

```python
edges = pe.arb_racing(categories="horse", min_edge_pct=1, verify=1)
```

## Endpoints covered

| Method | What it returns |
| --- | --- |
| `sports()` | Active sports + keys |
| `odds(sport_key, markets=...)` | Bookmaker odds for a sport |
| `best_odds(sport_key)` | Best price per selection + arb flags |
| `racing_next_to_go(categories=...)` | Next races, runners + prices |
| `racing_events(hours_ahead=...)` | Upcoming race list |
| `arb_sports(min_profit_pct=...)` | Surebets + best-odds overlays |
| `arb_racing(categories=...)` | Back/lay arb vs Betfair |
| `arb_lines(sport_key=...)` | Spreads/totals line arbs |
| `arb_best_prices(sport_key=...)` | Best + worst + average per book |
| `value_promos(book=...)` | EV-ranked promo board |
| `usage()` | Credits used / remaining |
| `data_quality_summary()` | Connector freshness + audit status |
| `health()` / `uptime()` | Service health |

## Error handling

```python
from puntersedge import PuntersEdge, RateLimitError, AuthenticationError

pe = PuntersEdge("YOUR_API_KEY")
try:
    pe.best_odds("nrl")
except AuthenticationError:
    print("Bad or missing API key")
except RateLimitError:
    print("Monthly credit cap or rate limit hit")
```

5xx responses are retried automatically (configurable via `retries=`).

## Configuration

```python
pe = PuntersEdge(
    api_key="YOUR_API_KEY",
    base_url="https://api.puntersedge.online/v1",  # default
    timeout=15.0,
    retries=2,
)
```

## Links

- 🔑 **Free API key** — https://puntersedge.online/api-platform#signup
- 📚 **Documentation** — https://puntersedge.online/developers
- 💳 **Pricing** — https://puntersedge.online/api/pricing
- 🧮 **Live sandbox (no key)** — https://puntersedge.online/api-platform#trylive
- 📮 **Postman collection** — https://api.puntersedge.online/postman.json (Postman → Import → Link)

## Disclaimer

Data is provided for informational and analytical use only. Odds move — verify each leg before staking. Not financial advice. 18+. Gamble responsibly — [Gambling Help 1800 858 858](https://www.gamblinghelponline.org.au).

## License

MIT © PuntersEdge
