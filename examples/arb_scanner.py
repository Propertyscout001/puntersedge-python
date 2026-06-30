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

print("\n== Racing back/lay vs Betfair ==")
for edge in pe.arb_racing(categories="horse", min_edge_pct=1, verify=1):
    print(f"  {edge}")
