"""Runtime configuration driven by environment variables.

This module centralises everything that can be tuned at deployment time. It
is imported for its side effect of validating the two required secrets
(``OPTIMUS_CLASS_PASSWORD`` and ``OPTIMUS_ADMIN_KEY``) — if either is missing
or empty, import fails with a clear :class:`RuntimeError` and the server never
starts. This prevents accidentally exposing a public instance that is still
using shipped defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "ADMIN_KEY",
    "BATTERY_CHARGE_EFF",
    "BATTERY_CYCLE_LIFE",
    "BATTERY_DISCHARGE_EFF",
    "BATTERY_EFF_DEGRADATION_FACTOR",
    "BATTERY_EOL_SOH",
    "BATTERY_MAX_MW",
    "BATTERY_MAX_MWH",
    "BATTERY_ROUND_TRIP_EFF",
    "BATTERY_SELF_DISCHARGE_RATE",
    "BATTERY_SOC_TAPER_MIN_MULT",
    "BATTERY_SOC_TAPER_START",
    "BATTERY_START_MWH",
    "BATTERY_START_SOH",
    "CLASS_PASSWORD",
    "DA_CLEARING_HOUR",
    "DB_PATH",
    "STARTING_CASH",
    "STARTING_REF_PRICE",
    "TICK_INTERVAL_SECONDS",
    "TRADE_RATE_LIMIT",
    "TRADE_RATE_WINDOW",
    "require_env",
]


def require_env(name: str) -> str:
    """Return the value of a required environment variable or raise.

    Missing these secrets is a misconfiguration that must fail fast at import
    time, before the background game loop starts accepting connections.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is required but is unset or empty.\n"
            "See .env.example for documentation. For Docker, pass it via "
            f"`-e {name}=...` or an env_file. For local development, set it "
            "in your shell before running `uvicorn main:app`."
        )
    return value


# ── Secrets (required) ────────────────────────────────────────────────────
# Class password — every student uses this to join.
CLASS_PASSWORD: str = require_env("OPTIMUS_CLASS_PASSWORD")

# Admin secret for /admin/* endpoints.
ADMIN_KEY: str = require_env("OPTIMUS_ADMIN_KEY")

# ── Battery specs ─────────────────────────────────────────────────────────
BATTERY_MAX_MWH: int = int(os.environ.get("OPTIMUS_BATTERY_MAX_MWH", "50000"))
"""Total energy capacity — 50 GWh."""

BATTERY_MAX_MW: int = int(os.environ.get("OPTIMUS_BATTERY_MAX_MW", "10000"))
"""Max charge/discharge rate per hour — 10 GW."""

BATTERY_START_MWH: int = int(os.environ.get("OPTIMUS_BATTERY_START_MWH", "25000"))
"""Starting SoC — 50%."""

# ── Battery physics (realistic constraints) ───────────────────────────────
# Round-trip efficiency: 90% total, split symmetrically as sqrt(0.90) per leg.
BATTERY_ROUND_TRIP_EFF: float = 0.90
BATTERY_CHARGE_EFF: float = 0.90**0.5  # ~0.9487
BATTERY_DISCHARGE_EFF: float = 0.90**0.5  # ~0.9487

# Self-discharge: 0.02%/h ≈ 0.48%/day — realistic for Li-ion grid storage.
BATTERY_SELF_DISCHARGE_RATE: float = 0.0002

# SoC-dependent charge taper (real Li-ion slows above ~80% SoC).
BATTERY_SOC_TAPER_START: float = 0.80
BATTERY_SOC_TAPER_MIN_MULT: float = 0.20

# Cycle-based State of Health degradation.
BATTERY_CYCLE_LIFE: int = 5000  # Equivalent full cycles to reach EOL
BATTERY_EOL_SOH: float = 0.70  # 70% SoH = end of useful life
BATTERY_START_SOH: float = 1.0
BATTERY_EFF_DEGRADATION_FACTOR: float = 0.10

# ── Economy ───────────────────────────────────────────────────────────────
STARTING_CASH: float = float(os.environ.get("OPTIMUS_STARTING_CASH", "0.0"))

# Reference price for mark-to-market PnL. Must match ZONES["DE"]["base_price"]
# so every player's initial MTM PnL is exactly 0.
STARTING_REF_PRICE: float = float(os.environ.get("OPTIMUS_STARTING_REF_PRICE", "45.0"))

# ── Tick timing ───────────────────────────────────────────────────────────
TICK_INTERVAL_SECONDS: float = float(os.environ.get("OPTIMUS_TICK_INTERVAL", "1.0"))

# ── Rate limiting ─────────────────────────────────────────────────────────
TRADE_RATE_LIMIT: int = int(os.environ.get("OPTIMUS_TRADE_RATE_LIMIT", "2"))
TRADE_RATE_WINDOW: float = float(os.environ.get("OPTIMUS_TRADE_RATE_WINDOW", "1.0"))

# ── Database ──────────────────────────────────────────────────────────────
# Anchored to the repository root so the same DB is used regardless of the
# working directory when starting the server.
_REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH: str = os.environ.get(
    "OPTIMUS_DB_PATH",
    str(_REPO_ROOT / "battery_trader.db"),
)

# ── Day-ahead market ──────────────────────────────────────────────────────
DA_CLEARING_HOUR: int = 12
"""Hour of the simulated day at which the DA auction clears."""
