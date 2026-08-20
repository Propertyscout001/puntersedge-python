# Changelog

## 0.2.1

Three fixes, all from the first run of the scanner against the live API.

- **The scanner misreported its own diagnosis.** Enrichment called
  `/v1/sports/{key}/odds` without stating `maxAgeMinutes`, so it inherited the API
  default of 360 — which *drops* bookmaker markets older than six hours before their
  age can be read. A stale price therefore arrived as no price, and the scanner said
  `unknown_age` ("the feed gave me no age") for a leg whose real problem was
  `stale_quote` ("this price is ancient"). Two different problems with two different
  fixes, and the tool named the wrong one. It now asks for the widest window the API
  allows, so its own gate is the only thing rejecting on age.

- **The default freshness budget could never pass anything.** `max_quote_age_s` was
  120s, carried over from a racing feed that refreshes every ~15s. The sports feed
  refreshes every 900s and its measured median price age is 572s, so the default
  rejected 100% of live data — which reads as a quiet market, not as a broken setting.
  Now 900s, one upstream poll cycle. That is the feed's ceiling, not a recommendation:
  a price with a median age of ~9.5 minutes is not a tight arbitrage signal, and no
  setting changes that.

- **API keys are UUIDs, not `pe_`-prefixed.** The README and the "no API key found"
  error both showed `api_key = pe_...`, so anyone checking a real key against the
  documented format concluded theirs was wrong. Examples are now UUID-shaped.

Note for anyone scanning sports: `/v1/arb/sports` applies no freshness filter of its
own, so it can surface arbs built on legs the odds endpoint will not return at any age
window. This release refuses those rather than reporting them, which is why a scan may
return nothing where the raw endpoint shows candidates.

## 0.2.0

**Breaking.** This release replaces the client that shipped as 0.1.0 on PyPI
(2026-06-07). If you are upgrading from that version, the imports and method
names have changed.

### Your API key no longer belongs in your code

`PuntersEdge()` now takes no argument and resolves the key itself, per setting, from
the `api_key=` argument, then `$PUNTERSEDGE_API_KEY`, then `$PUNTERSEDGE_CONFIG_FILE`,
then `~/.config/puntersedge/config` (`%APPDATA%\puntersedge\config` on Windows). If
none has one, the error names every source it tried and what each reported, so a wrong
path, an empty variable and a wrong section do not all look like a 401.
`PuntersEdge("KEY")` still works.

### Seven ways the client leaked your API key, now fixed

Each was reproduced against this package with a sentinel key before being changed.

- `raise ... from None` on a network failure. Re-raising inside the `except` kept the
  urllib3 frames attached, and their locals hold the request headers — **a plain
  `pytest` run with no flags printed the key twice** on a connection failure.
- Exceptions no longer retain the live request. `pickle.dumps(exc)` carried the key in
  plaintext while `str()` and `repr()` looked clean.
- Redirects are refused. `requests` strips only `Authorization`, `Proxy-Authorization`
  and `Cookie` across a cross-host redirect — never a custom auth header — so a 302
  delivered `X-API-Key` to the destination.
- `base_url` must be https (localhost excepted); plain http sent the key in clear.
- The key is no longer a plain attribute, and clients are not picklable.
- `rotate_key()` updates the session, so calls after a rotation keep working.
- Server error bodies are redacted before they reach an exception message.

### New: an arbitrage toolkit

`puntersedge.arb` — detection gates, a credit-aware scanner, stake sizing, an
append-only ledger, throttled alerts, and a `puntersedge-arb` command line. It never
places a bet, holds a bookmaker credential, or touches an account. See the README.

No profit is claimed or implied: book-vs-book arbitrage on Australian markets is thin,
intermittent, and self-limiting, because bookmakers restrict accounts that do it.

### Upgrading from 0.1.0

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
