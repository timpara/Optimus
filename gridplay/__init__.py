"""gridplay — Battery Trader Sim.

A multiplayer educational energy trading game. Students manage a 50 GWh / 10 GW
grid battery in Germany and trade on a simulated multi-zone European spot
market.

This package exposes the pure-data configuration that drives the simulation
(market zones, interconnectors, demand curves, event templates) and the
env-backed settings for runtime tuning. The simulation engine itself still
lives in ``main.py`` at the repository root during the ongoing refactor; see
``docs/ARCHITECTURE.md`` for the plan.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("gridplay")
except PackageNotFoundError:  # pragma: no cover — not installed (e.g. source checkout)
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
