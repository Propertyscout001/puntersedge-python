"""
puntersedge — official Python client for the PuntersEdge Australian Sports Odds API.

Live Australian bookmaker odds, racing next-to-go, best-odds comparison and
pre-computed arbitrage/value signals as clean JSON.

Free API key (no credit card): https://puntersedge.online/api-platform#signup
Docs: https://puntersedge.online/developers
"""
from .client import PuntersEdge
from .exceptions import (
    AuthenticationError,
    NotFoundError,
    PuntersEdgeError,
    RateLimitError,
    ServerError,
)

__version__ = "0.1.0"
__all__ = [
    "PuntersEdge",
    "PuntersEdgeError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "ServerError",
]
