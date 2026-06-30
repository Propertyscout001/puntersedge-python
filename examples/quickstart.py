"""Quickstart: list sports, pull NRL odds, compare best prices.

Run:  PUNTERSEDGE_API_KEY=xxx python examples/quickstart.py
Free key: https://puntersedge.online/api-platform#signup
"""
import os

from puntersedge import PuntersEdge

pe = PuntersEdge(os.environ["PUNTERSEDGE_API_KEY"])

print("Active sports:")
for sport in pe.sports():
    print(f"  {sport['key']:<10} {sport['title']}")

print("\nBest NRL prices across all books:")
for event in pe.best_odds("nrl"):
    print(f"\n  {event['home_team']} v {event['away_team']}")
    for sel in event.get("selections", []):
        print(f"    {sel['name']:<22} {sel['best_price']:>5} @ {sel['best_bookmaker']}")

print("\nCredits:", pe.usage())
