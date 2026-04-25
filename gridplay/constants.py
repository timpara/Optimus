"""Static configuration tables that define the simulated European spot market.

These are pure data — no side effects at import time. Constants here are
intentionally read-only; runtime overrides belong in :mod:`gridplay.config`.

The values are simplified but directionally correct: France really is
nuclear-dominated (stable prices), Denmark really is wind-dominated (volatile
prices), and Poland really is coal-heavy. Capacities are approximate NTC
values that vary by season/direction in reality.
"""

from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Market zone definitions
# ─────────────────────────────────────────────────────────────────────────────
# Each zone represents a country's electricity market with its own generation
# mix. The key parameters that drive each zone's price behaviour:
#
#   base_price:        Average price in EUR/MWh under normal conditions
#   wind_sensitivity:  How much wind generation pushes price DOWN (merit order)
#   solar_sensitivity: How much solar generation pushes price DOWN
#   demand_elasticity: How much demand swings affect price
#   wind_volatility:   How wildly wind speed changes (Ornstein–Uhlenbeck σ)
#   wind_mean:         Long-term average wind speed (m/s)
#   peak_demand_gw:    Approximate peak demand for zone sizing
#   latitude:          Used for the deterministic solar-irradiance model
# ─────────────────────────────────────────────────────────────────────────────

ZONES: dict[str, dict[str, Any]] = {
    "DE": {
        "name": "Germany",
        "base_price": 45.0,  # balanced mix (wind+solar+gas+coal)
        "wind_sensitivity": 12.0,  # significant wind capacity (60+ GW)
        "solar_sensitivity": 10.0,  # significant solar (~60 GW)
        "demand_elasticity": 35.0,  # large industrial demand swings
        "wind_volatility": 2.5,
        "wind_mean": 6.0,
        "peak_demand_gw": 80.0,
        "latitude": 51.0,
    },
    "FR": {
        "name": "France",
        "base_price": 50.0,  # nuclear baseload is cheap but inflexible
        "wind_sensitivity": 3.0,
        "solar_sensitivity": 4.0,
        "demand_elasticity": 20.0,  # electric heating causes demand spikes
        "wind_volatility": 1.5,
        "wind_mean": 5.0,
        "peak_demand_gw": 90.0,  # lots of electric heating
        "latitude": 47.0,
    },
    "NL": {
        "name": "Netherlands",
        "base_price": 55.0,  # gas-heavy generation is expensive
        "wind_sensitivity": 8.0,  # growing offshore wind
        "solar_sensitivity": 5.0,
        "demand_elasticity": 30.0,  # gas plants are the marginal unit
        "wind_volatility": 3.0,  # North Sea wind is gusty
        "wind_mean": 7.0,
        "peak_demand_gw": 20.0,
        "latitude": 52.0,
    },
    "DK": {
        "name": "Denmark",
        "base_price": 40.0,  # lots of cheap wind
        "wind_sensitivity": 20.0,  # wind is >50% of generation
        "solar_sensitivity": 2.0,
        "demand_elasticity": 15.0,  # small market
        "wind_volatility": 4.0,  # very volatile — North Sea storms
        "wind_mean": 8.0,
        "peak_demand_gw": 6.0,
        "latitude": 56.0,
    },
    "PL": {
        "name": "Poland",
        "base_price": 60.0,  # coal-dominated, high CO₂ costs
        "wind_sensitivity": 5.0,
        "solar_sensitivity": 3.0,
        "demand_elasticity": 25.0,
        "wind_volatility": 2.0,
        "wind_mean": 5.5,
        "peak_demand_gw": 25.0,
        "latitude": 52.0,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Interconnector definitions
# ─────────────────────────────────────────────────────────────────────────────
# Interconnectors are the physical transmission lines (HVDC cables, AC ties)
# between countries. Each has a hard capacity limit in MW. When desired flow
# exceeds capacity the line is "congested" and price spreads between zones
# persist — that's the key educational mechanic.
# ─────────────────────────────────────────────────────────────────────────────

INTERCONNECTORS: dict[str, dict[str, Any]] = {
    "DE-FR": {
        "zones": ("DE", "FR"),
        "max_capacity_mw": 4000,  # 4.0 GW — large cross-border capacity
        "flow_sensitivity": 150.0,  # MW per EUR/MWh price difference
    },
    "DE-NL": {
        "zones": ("DE", "NL"),
        "max_capacity_mw": 3500,  # 3.5 GW
        "flow_sensitivity": 120.0,
    },
    "DE-DK": {
        "zones": ("DE", "DK"),
        "max_capacity_mw": 2500,  # 2.5 GW — the bottleneck students learn about
        "flow_sensitivity": 100.0,
    },
    "DE-PL": {
        "zones": ("DE", "PL"),
        "max_capacity_mw": 3000,  # 3.0 GW
        "flow_sensitivity": 130.0,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Duck curve — 24-hour demand profile
# ─────────────────────────────────────────────────────────────────────────────
# Multipliers (0.0–1.0) on each zone's peak demand per hour of day. The name
# comes from the duck-shaped net-demand curve created when solar depresses
# midday demand and peaks return in the evening.
# ─────────────────────────────────────────────────────────────────────────────

DUCK_CURVE: list[float] = [
    # 00-05: night trough
    0.55, 0.50, 0.48, 0.47, 0.48, 0.52,
    # 06-11: morning ramp → solar dip
    0.60, 0.70, 0.80, 0.85, 0.82, 0.75,
    # 12-17: solar belly → evening ramp
    0.70, 0.68, 0.70, 0.75, 0.85, 0.95,
    # 18-23: peak → wind down
    1.00, 0.97, 0.90, 0.80, 0.70, 0.60,
]  # fmt: skip

# ─────────────────────────────────────────────────────────────────────────────
# Game event templates
# ─────────────────────────────────────────────────────────────────────────────
# Random disruptions: plant outages, heat waves, carbon spikes, cable faults.
# Each template drives market shocks that teach students that energy trading
# isn't just about weather — policy, supply chains, and demand surprises are
# constant. Drawn by the event engine using per-template probabilities.
# ─────────────────────────────────────────────────────────────────────────────

EVENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "type": "plant_outage",
        "headlines": [
            "BREAKING: {zone} nuclear plant offline — unplanned maintenance",
            "ALERT: Major gas-fired unit tripped in {zone} — 2 GW offline",
            "FLASH: {zone} coal plant emergency shutdown — emissions fault",
        ],
        "zones": ["DE", "FR", "PL"],
        "price_mod": 15.0,
        "duration_range": (8, 24),
        "probability": 0.015,
    },
    {
        "type": "demand_shock_heat",
        "headlines": [
            "HEATWAVE: {zone} temperatures soar — AC demand surges",
            "EXTREME HEAT: {zone} grid operator issues demand warning",
        ],
        "zones": ["DE", "FR", "NL"],
        "price_mod": 12.0,
        "duration_range": (12, 36),
        "probability": 0.01,
    },
    {
        "type": "demand_shock_cold",
        "headlines": [
            "COLD SNAP: {zone} heating demand spikes as temperatures plunge",
            "FREEZE WARNING: {zone} electric heating load at seasonal high",
        ],
        "zones": ["DE", "FR", "PL"],
        "price_mod": 10.0,
        "duration_range": (12, 32),
        "probability": 0.01,
    },
    {
        "type": "carbon_price_spike",
        "headlines": [
            "EU ETS SURGE: Carbon price jumps €20/t — coal plants squeezed",
            "CARBON SHOCK: EU allowance auction clears at record high",
        ],
        "zones": ["ALL"],
        "price_mod": 8.0,
        "duration_range": (24, 72),
        "probability": 0.005,
    },
    {
        "type": "interconnector_fault",
        "headlines": [
            "CABLE FAULT: {ic} interconnector capacity reduced to 50%",
            "TRANSMISSION: {ic} line maintenance — capacity halved",
        ],
        "interconnectors": ["DE-FR", "DE-NL", "DE-DK", "DE-PL"],
        "price_mod": 0.0,
        "duration_range": (6, 20),
        "probability": 0.008,
    },
    {
        "type": "renewable_subsidy",
        "headlines": [
            "POLICY: {zone} announces emergency renewable curtailment subsidy",
            "REGULATION: {zone} grid operator orders wind farm curtailment",
        ],
        "zones": ["DE", "DK", "NL"],
        "price_mod": -8.0,
        "duration_range": (8, 20),
        "probability": 0.008,
    },
    {
        "type": "lng_cargo_arrival",
        "headlines": [
            "LNG: Spot cargo arrives at {zone} — gas prices ease",
            "SUPPLY: Unscheduled LNG delivery to {zone} terminal",
        ],
        "zones": ["DE", "NL"],
        "price_mod": -6.0,
        "duration_range": (12, 28),
        "probability": 0.01,
    },
    {
        "type": "grid_emergency",
        "headlines": [
            "EMERGENCY: {zone} TSO activates reserves — frequency deviation",
            "GRID ALERT: {zone} system operator calls for emergency generation",
        ],
        "zones": ["DE", "FR", "PL"],
        "price_mod": 25.0,
        "duration_range": (4, 10),
        "probability": 0.005,
    },
]

# Carbon intensity per zone — used to scale carbon price spike effects.
# Higher = more coal/gas in the generation mix = more affected by carbon prices.
CARBON_INTENSITY: dict[str, float] = {
    "DE": 0.6,  # mixed (coal + gas + renewables)
    "FR": 0.15,  # nuclear-dominated — barely affected
    "NL": 0.7,  # gas-heavy
    "DK": 0.3,  # wind-heavy, some gas backup
    "PL": 1.0,  # coal-dominated — maximum impact
}

# ─────────────────────────────────────────────────────────────────────────────
# Forecast model constants
# ─────────────────────────────────────────────────────────────────────────────

FORECAST_HORIZON: int = 6
"""How many hours ahead the weather forecast looks."""

SURPRISE_AMPLIFIER: float = 1.5
"""Price-impact multiplier for forecast-vs-actual weather deviations.

Values > 1.0 model the real-world tendency of markets to overreact to
surprises — the larger the deviation from the forecast, the more aggressively
prices move because traders have to rebalance positions in real time.
"""

FORECAST_NOISE_SCALE: dict[str, float] = {
    "DE": 1.0,  # baseline
    "FR": 0.5,  # very predictable (nuclear-dominated, low renewables)
    "NL": 1.3,  # offshore wind is hard to forecast
    "DK": 1.8,  # hardest — North Sea storms are chaotic
    "PL": 0.7,  # mostly coal, moderate wind — fairly predictable
}

__all__ = [
    "CARBON_INTENSITY",
    "DUCK_CURVE",
    "EVENT_TEMPLATES",
    "FORECAST_HORIZON",
    "FORECAST_NOISE_SCALE",
    "INTERCONNECTORS",
    "SURPRISE_AMPLIFIER",
    "ZONES",
]
