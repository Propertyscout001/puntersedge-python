# Changelog

## 0.2.0

**Breaking.** This release replaces the client that shipped as 0.1.0 on PyPI
(2026-06-07). If you are upgrading from that version, the imports and method
names have changed:

| 0.1.0 | 0.2.0 |
| --- | --- |
| `from puntersedge import PuntersEdgeClient` | `from puntersedge import PuntersEdge` |
| `AsyncPuntersEdgeClient` | *(removed — this release is sync-only)* |
| `client.get_sports()` | `client.sports()` |
| `client.get_odds(sport_key)` | `client.odds(sport_key)` |
| `client.get_best_odds(sport_key)` | `client.best_odds(sport_key)` |
| `client.get_racing()` | `client.racing_next_to_go()` |
| `client.get_arb()` | `client.arb_sports()` |
| `client.get_usage()` | `client.usage()` |
| `client.get_health()` | `client.health()` |

- Endpoint coverage goes from 7 to 20, adding racing events, odds history and
  movements, popular markets, the full arbitrage set (sports / racing / lines /
  best-prices), value promos, usage analytics, key info and rotation,
  data-quality summary and uptime.
- Switched the HTTP dependency from `httpx` to `requests`.
- Base URL is now `https://api.puntersedge.online/v1` (was
  `https://puntersedge.online/api`). Both routes are live; the former is the
  documented one.
- Typed exceptions (`AuthenticationError`, `RateLimitError`, `NotFoundError`,
  `ServerError`) with automatic retry on 5xx.
- Every one of the 20 endpoints was validated against the live OpenAPI schema
  at `https://api.puntersedge.online/openapi.json` before release.
- Corrected the documented free tier to 1,500 credits/month (was 2,500).

## 0.1.0
- Initial release.
- `PuntersEdge` client covering sports, odds, best-odds, racing next-to-go/events,
  arbitrage (sports / racing / lines / best-prices), value promos, usage,
  data-quality, and health/uptime endpoints.
- Typed exceptions (`AuthenticationError`, `RateLimitError`, `NotFoundError`,
  `ServerError`) and automatic retry on 5xx.
