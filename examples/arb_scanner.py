"""Arbitrage scanner — print surebets across Australian books + racing back/lay.

Run:  PUNTERSEDGE_API_KEY=xxx python examples/arb_scanner.py
"""
import os

from puntersedge import PuntersEdge

pe = PuntersEdge(os.environ["PUNTERSEDGE_API_KEY"])

print("== Sports arbitrage ==")
found = 0
for arb in pe.arb_sports(min_profit_pct=0):
    if not arb.get("is_arb"):
        continue
    found += 1
    print(f"\n{arb['home_team']} v {arb['away_team']}  ({arb['sport_key']})  +{arb['arb_pct']}%")
    for leg in arb.get("optimal_stakes", []):
        print(f"   ${leg['stake']:>6} on {leg['name']:<22} @ {leg['bookmaker']}")
if not found:
    print("  No live sports arbs right now (efficient markets) — try again near game time.")

# arb_racing() is withheld (410) on every customer key — the Betfair lay side needs a data
# licence — so this script used to die here. racing_best_odds() is the live equivalent:
# market_percentage under 100 is cross-book value that needs no exchange.
print("\n== Racing cross-book value (market_percentage < 100) ==")
for race in pe.racing_best_odds(categories="horse", num_races=5):
    mp = race.get("market_percentage")
    flag = "  <-- beats the field" if mp is not None and mp < 100 else ""
    print(f"  {race.get('venue')} R{race.get('race_number')}: market {mp}%{flag}")
