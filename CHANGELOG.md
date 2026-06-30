# Changelog

## 0.1.0
- Initial release.
- `PuntersEdge` client covering sports, odds, best-odds, racing next-to-go/events,
  arbitrage (sports / racing / lines / best-prices), value promos, usage,
  data-quality, and health/uptime endpoints.
- Typed exceptions (`AuthenticationError`, `RateLimitError`, `NotFoundError`,
  `ServerError`) and automatic retry on 5xx.
