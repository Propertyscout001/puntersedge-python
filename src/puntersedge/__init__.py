"""
puntersedge — official Python client for the PuntersEdge Australian Sports Odds API.

Live Australian bookmaker odds, racing next-to-go, best-odds comparison and
pre-computed arbitrage/value signals as clean JSON.

This package never holds bookmaker credentials, never places bets, and never operates a
betting account. It reads odds and computes sizing; you place every bet yourself in your
own session.

Free API key (no credit card): https://puntersedge.online/api-platform#signup
Docs: https://puntersedge.online/developers
"""
from .client import PuntersEdge
from .config import ConfigChain, default_config_path, resolve_api_key
from .exceptions import (
    ApiKeyError,
    AuthenticationError,
    ConfigError,
    NotFoundError,
    PuntersEdgeError,
    RateLimitError,
    ServerError,
)

__version__ = "0.2.1"
__all__ = [
    "PuntersEdge",
    "PuntersEdgeError",
    "AuthenticationError",
    "RateLimitError",
    "NotFoundError",
    "ServerError",
    "ConfigError",
    "ApiKeyError",
    "resolve_api_key",
    "default_config_path",
    "ConfigChain",
]
