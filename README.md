# PuntersEdge — Australian Sports Odds API (Python client)

Official Python client for the [**PuntersEdge Australian Sports Odds API**](https://puntersedge.online/api-platform) — live bookmaker odds across 11 Australian books, racing next-to-go, best-odds comparison, and pre-computed **arbitrage / value** signals, all as clean JSON.

> Get a **free API key** (1,500 credits/month, no credit card) → **[puntersedge.online/api-platform](https://puntersedge.online/api-platform#signup)**

[![PyPI](https://img.shields.io/pypi/v/puntersedge.svg)](https://pypi.org/project/puntersedge/)
[![Python](https://img.shields.io/pypi/pyversions/puntersedge.svg)](https://pypi.org/project/puntersedge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Why this API

Most "sports odds API" products have thin Australian coverage. PuntersEdge is **Australian-first**: Sportsbet, TAB, Neds, Ladbrokes, Unibet, PointsBet, Betr, BetRight, NextBet, Palmerbet and TABtouch — eleven Australian books on racing, six of them on sports — across AFL, NRL, NBA, WNBA, tennis and cricket, plus **horse / greyhound / harness racing**.

- 🟢 **Live bookmaker odds** — one REST endpoint, 11 AU books
- 🏇 **Racing next-to-go** — runners + prices for horse, greyhound, harness
- ⚖️ **Best-odds comparison** — best price per selection across every book
- 🎯 **Arbitrage, pre-computed** — surebets and spreads/totals line arbs, with suggested stake splits already calculated for you to place yourself. Racing back/lay against the exchange is withheld pending a Betfair data licence; use `racing_best_odds()` for cross-book racing value
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

pe = PuntersEdge()  # https://puntersedge.online/api-platform#signup

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

pe = PuntersEdge()
for arb in pe.arb_sports(min_profit_pct=0):
    if arb.get("is_arb"):
        print(f"{arb['home_team']} v {arb['away_team']}: {arb['arb_pct']}%")
        for leg in arb["optimal_stakes"]:
            print(f"   stake ${leg['stake']} on {leg['name']} @ {leg['bookmaker']}")
```

Racing cross-book value (no exchange needed):

```python
races = pe.racing_best_odds(categories="horse", num_races=5)
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
| `racing_best_odds(categories=...)` | Best win/place/tote per runner across the AU books |
| `arb_racing(...)` | **Withheld** — 410 pending a Betfair data licence |
| `arb_lines(sport_key=...)` | Spreads/totals line arbs |
| `arb_best_prices(sport_key=...)` | Best + worst + average per book |
| `value_promos(book=...)` | EV-ranked promo board |
| `usage()` | Credits used / remaining |
| `data_quality_summary()` | Connector freshness + audit status |
| `health()` / `uptime()` | Service health |

## Error handling

```python
from puntersedge import PuntersEdge, RateLimitError, AuthenticationError

pe = PuntersEdge()
try:
    pe.best_odds("nrl")
except AuthenticationError:
    print("Bad or missing API key")
except RateLimitError:
    print("Monthly credit cap or rate limit hit")
```

5xx responses are retried automatically (configurable via `retries=`).

## Configuration

You never have to put your key in your code. `PuntersEdge()` finds it for you, checking
each source **per setting** — so a `base_url` in your file still applies when your key comes
from the environment:

| | source |
|---|---|
| 1 | the `api_key=` argument |
| 2 | `$PUNTERSEDGE_API_KEY` |
| 3 | `$PUNTERSEDGE_CONFIG_FILE`, if set — then that file only |
| 4 | `~/.config/puntersedge/config` (`%APPDATA%\puntersedge\config` on Windows) |

Create the file once:

```bash
mkdir -p ~/.config/puntersedge
printf '[puntersedge]\napi_key = 3f8b1c04-5e7a-4d21-9b6e-0a2c8d5f1e93\n' > ~/.config/puntersedge/config
chmod 600 ~/.config/puntersedge/config
```

```ini
[puntersedge]
api_key  = 3f8b1c04-5e7a-4d21-9b6e-0a2c8d5f1e93
base_url = https://api.puntersedge.online/v1
timeout  = 15
retries  = 2

[arb]
bettable_books  = sportsbet, tab, neds
min_edge_pct    = 0.5
max_quote_age_s = 120
```

```python
pe = PuntersEdge()                      # reads [puntersedge]
cfg = GateConfig.load()                 # reads [arb] — never sees your key
print(pe.key_source)                    # "$PUNTERSEDGE_API_KEY" — the source, never the key
```

If no key is found, the error names every source it tried and what each one reported, so
"wrong file", "empty variable" and "wrong section" do not all look like a 401.

Explicit arguments still work — `PuntersEdge(api_key="...", timeout=30)` — and override
everything else.

**A note on the config file.** It is checked before it is read: refused outright if another
user owns it or can write to it, and warned about if others can read it (`chmod 600` fixes
that). It holds your PuntersEdge API key and, if you use alerts, one webhook URL — which is
also a credential, since anyone holding it can post to your channel. It has exactly three
sections, and any other section is a startup error.

## Scanning and stake sizing

```python
from puntersedge import PuntersEdge
from puntersedge.arb import GateConfig, Scanner, size

scanner = Scanner(
    PuntersEdge(),
    GateConfig(bettable_books={"sportsbet", "tab", "neds"}),
    sports=["afl", "nrl"],
    credit_budget=500,
)

result = scanner.poll()
print(result.summary())
# 41 candidates | 1 passed | 5 credits | refused: no_cross=28, server_not_arb=9, stale_quote=3

for opp in result.arbs:
    plan = size(opp, total=200, minimums={"sportsbet": 5.0})
    if plan:
        for leg in plan.legs:
            print(f"  {leg.book:12} {leg.selection:24} ${leg.stake:7.2f} @ {leg.odds}")
        print(f"  guaranteed ${plan.profit:.2f} ({plan.profit_pct:.2f}%)")
    else:
        print(" ", plan.reason)
```

**`plan.profit` is the worst case after rounding** — what you are actually guaranteed, not
the textbook figure. That distinction is the whole point of the module: rounding an equal-
profit split to whole dollars produced a *guaranteed loss* in 6.9% of thin arbs we tested,
and was worse than optimal in 56%. `size()` searches for the plan that maximises your worst
case — verified against brute force with zero disagreements — and refuses outright when
nothing clears zero. `total` is a **cap**: it will stake less than you offered if that pays
better, and never more.

**When a poll finds nothing, ask it why.** `result.diagnosis()` distinguishes an efficient
market from a broken scanner — a filter matching no sports, enrichment failing, or every
candidate priced at books you don't hold. A count on its own cannot.

### Credits — read this before you set an interval

A poll costs 3 credits, plus 1 per sport that needs a freshness check. Prices ages are not
in the arb response, so they have to be fetched separately.

| interval | credits/month | vs free tier (1,500) |
|---|---|---|
| 60s | ~259,000 | 173× over |
| 5 min | ~52,000 | 35× over |
| 15 min | ~17,000 | 12× over |
| 1 hour | ~4,300 | 3× over |
| 3 hours | ~1,400 | fits |

**The free tier cannot run a live scanner.** That is arithmetic, not a limitation we chose.
A useful sports scanner wants Starter or above. `scanner.budget_advice(interval)` prints the
number for your configuration, and `credit_budget=` makes the scanner refuse to exceed a cap
rather than silently draining your month.

Two things keep the bill down automatically: gates that need only the arb payload run
*first*, so only sports with surviving candidates get paid for; and there is no point
polling faster than 15 minutes, because that is how often the upstream sports feed refreshes
— a faster loop buys nothing but spend.

## Command line

```bash
pip install puntersedge

puntersedge-arb config                          # where your key comes from (never the key)
puntersedge-arb scan --sports afl,nrl --stake 200
puntersedge-arb scan --watch 900 --budget 5000 --record
puntersedge-arb ledger pnl
```

`scan` prints its credit cost before spending anything, and refuses a `--watch` interval
faster than the 900s upstream refresh unless you pass `--yes` — a faster loop cannot surface
anything new, it only spends. Exit codes are distinct (`2` config, `3` credit budget), and
finding no arbs exits `0`, because an efficient market is not an error.

## The ledger

`--record` writes each sized arb to an append-only JSONL log. It keeps three things apart
that are easy to conflate and expensive to confuse:

```bash
puntersedge-arb ledger place  <bet_id> sportsbet "Lions" 50 2.10   # a leg you got on
puntersedge-arb ledger settle <bet_id> sportsbet 105               # what it returned
puntersedge-arb ledger pnl
```

```
arbitrage (all legs placed) : +12.40 over 8 positions, 1600.00 staked (0.78%)
UNHEDGED (a leg missed)     : +55.00 over 1 position, 50.00 staked — directional bets, NOT arbitrage
open                        : 2 positions, 400.00 at risk
planned, never placed       : 31 — no money moved, excluded from P&L
```

Three rules it enforces, each of which cost the author of this library real money to learn:

- **A plan is not a bet.** Recording an intention as a placement overstated one production
  system's conversion by 3.6×. Plans contribute nothing to P&L until you record what
  actually went on, per leg, at the stake and price you actually got.
- **A partly-placed arb is a punt.** If one leg is refused you are holding a directional bet
  you never intended. That is reported on its own line and never added to the arb figure —
  a naked leg that wins is not evidence the strategy works.
- **Duplicates collapse on read.** A double-written settlement once moved a published track
  record. Deduplicating on read rather than on write makes the total immune to any writer
  bug, including ones not yet written.

Stakes and returns are yours to record — nothing here places a bet or reads a bookmaker
account. The ledger lives in your XDG state directory at mode `600`, never the working
directory.

## Alerts

Opt-in, and off unless you configure it:

```ini
[alerts]
webhook_url  = https://discord.com/api/webhooks/...
min_edge_pct = 1.0
cooldown_s   = 3600
max_per_hour = 20
```

```bash
puntersedge-arb scan --watch 900 --alert
puntersedge-arb scan --alert-console     # print them instead of sending
puntersedge-arb scan --alert-dry-run     # exercise the throttle, deliver nothing
```

Your webhook URL is a credential — anyone holding it can post to your channel — so it lives
in the config file beside your API key, is wrapped so it cannot be printed by accident, and
never appears in a message, an error, or a log line. Errors quote the host and the status
code, never the URL.

Two things this gets right that are easy to get wrong:

- **Deduplication never compares the message text.** An alert's identity is the event and its
  legs — no odds, no edge, no age, no timestamp. Put any moving number in the compared text
  and "has this changed?" is true on every poll, so the throttle never engages. That failure
  sent one system in this estate ~24 identical emails a day for three days, with a correct,
  tested, 12-hourly throttle in place the whole time.
- **Quota counts what was sent, not what was attempted.** A suppressed alert, a failed
  webhook and a dry run all consume nothing and start no cooldown. Recording before sending
  makes a rejected call extend its own lockout, so the window can never be waited out.

Every poll prints the alert line even when it is all zeros — an alerter that has quietly
suppressed everything for an hour looks exactly like a quiet market otherwise.

## Boundaries

This package **never holds bookmaker credentials, never places bets, and never operates a
betting account.** It reads odds and computes sizing; you place every bet yourself in your
own session. There is nowhere in the config file to put a bookmaker login, and attempting
to add one is a startup error rather than a documented discouragement.

Racing back/lay arbitrage — backing at a bookmaker and laying on the Betfair exchange — is
**not available to API customers**. `/v1/arb/racing` and `/v1/racing/exchange` return HTTP
410 on every customer key, because the exchange side is withheld pending a Betfair data
licence. Use `racing_best_odds()` for cross-book racing value, which needs no exchange.

No profit is claimed or implied. Book-vs-book arbitrage on Australian markets is thin,
intermittent, and self-limiting — bookmakers restrict accounts that do it. The `arb` tools
here are instrumentation: they tell you which of the feed's candidates survive scrutiny,
and why the rest do not.

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
