"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  BATTERY TRADER SIM — main.py                                              ║
║  A multiplayer educational energy trading game                              ║
║                                                                            ║
║  Students play as traders managing a 50 GWh / 10 GW battery in Germany,    ║
║  buying and selling electricity on a simulated multi-zone European spot     ║
║  market. The game teaches market coupling, interconnector congestion,       ║
║  renewable intermittency, and the duck curve.                               ║
║                                                                            ║
║  Run:  uvicorn main:app --host 0.0.0.0 --port 8000 --reload               ║
║  Open: http://localhost:8000                                                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: CONSTANTS & CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
# All configuration has moved into the `optimus` package for clarity and
# testability; the names are re-exported here to keep the rest of this
# module unchanged during the ongoing refactor.
#
#   optimus.config    — env-backed runtime settings (fails fast on missing secrets)
#   optimus.constants — pure data: market zones, interconnectors, events, duck curve
# ─────────────────────────────────────────────────────────────────────────────

from optimus.config import (  # noqa: E402  (re-exports preserve legacy import paths)
    ADMIN_KEY,
    BATTERY_CHARGE_EFF,
    BATTERY_CYCLE_LIFE,
    BATTERY_DISCHARGE_EFF,
    BATTERY_EFF_DEGRADATION_FACTOR,
    BATTERY_EOL_SOH,
    BATTERY_MAX_MW,
    BATTERY_MAX_MWH,
    BATTERY_ROUND_TRIP_EFF,
    BATTERY_SELF_DISCHARGE_RATE,
    BATTERY_SOC_TAPER_MIN_MULT,
    BATTERY_SOC_TAPER_START,
    BATTERY_START_MWH,
    BATTERY_START_SOH,
    CLASS_PASSWORD,
    DA_CLEARING_HOUR,
    DB_PATH,
    STARTING_CASH,
    STARTING_REF_PRICE,
    TICK_INTERVAL_SECONDS,
    TRADE_RATE_LIMIT,
    TRADE_RATE_WINDOW,
)
from optimus.constants import (
    CARBON_INTENSITY,
    DUCK_CURVE,
    EVENT_TEMPLATES,
    FORECAST_HORIZON,
    FORECAST_NOISE_SCALE,
    INTERCONNECTORS,
    SURPRISE_AMPLIFIER,
    ZONES,
)


@dataclass
class GameEvent:
    """A single active game event affecting the market."""

    event_type: str
    headline: str
    affected_zones: list[str] = field(default_factory=list)
    affected_ic: str | None = None  # For interconnector faults
    price_mod: float = 0.0
    ticks_remaining: int = 0
    started_tick: int = 0
    started_day: int = 0


@dataclass
class DABid:
    """
    A player's bid in the Day-Ahead auction.

    MERIT ORDER CLEARING (simplified):
    ──────────────────────────────────
    In real DA markets, bids form a supply/demand curve:
      - BUY bids: "I want to buy X MW at up to €Y/MWh"
      - SELL bids: "I want to sell X MW at at least €Y/MWh"

    The clearing price is where supply meets demand.
    All accepted BUY bids pay the clearing price (not their bid price).
    All accepted SELL bids receive the clearing price.

    This is called "pay-as-cleared" or "uniform pricing" — it's why
    bidding your true valuation is the optimal strategy (Vickrey insight).

    In our simplified version:
      - Clearing price = model's forecast of next-day average DE price
      - BUY bids with bid_price >= clearing_price execute
      - SELL bids with bid_price <= clearing_price execute
      - Everyone trades at the clearing price (not their bid price)
    """

    player_id: str
    player_name: str
    action: str  # "BUY" or "SELL"
    mw: float  # Volume in MW
    bid_price: float  # Player's limit price in EUR/MWh
    submitted_tick: int = 0
    submitted_day: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: DATA CLASSES FOR GAME STATE
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WeatherState:
    """Per-zone weather conditions updated each tick."""

    wind_speed: float = 6.0  # m/s — drives wind generation
    solar_irradiance: float = 0.0  # 0.0 to 1.0 — drives solar generation
    temperature: float = 15.0  # °C — affects heating/cooling demand
    storm_active: bool = False  # True during wind storm events
    storm_ticks_remaining: int = 0

    # ── Forecast tracking fields (new) ──
    # These store "what the forecast predicted for THIS hour" so we can
    # compute the surprise component (actual - forecasted) for pricing.
    # Updated each tick by copying the prior tick's forecast[0] before
    # the new forecast is generated.
    forecasted_wind: float = 6.0  # What was predicted for current hour
    forecasted_solar: float = 0.0  # What was predicted for current hour


@dataclass
class InterconnectorState:
    """Live state of a single transmission line between two zones."""

    flow_mw: float = 0.0  # Positive = A→B, Negative = B→A
    max_capacity_mw: float = 0.0
    is_congested: bool = False  # True when |flow| >= 95% of max capacity
    zone_a: str = ""
    zone_b: str = ""


@dataclass
class GameState:
    """
    The single global game state object. Updated by the background loop
    every tick (1 second). Read by all connected WebSocket clients.

    This is NOT stored in the database — it lives in memory for speed.
    Only price_history and player data are persisted to SQLite.
    """

    tick: int = 0  # Current hour (0-23)
    day: int = 1  # Current day number
    running: bool = True  # Game loop control flag
    paused: bool = False  # Pause flag (admin-controlled, reversible)

    # Game speed multiplier (admin-controlled).
    # 1.0 = normal (1 real second per tick), 2.0 = half speed (2s per tick),
    # 0.5 = double speed (0.5s per tick).  Clamped to [0.5, 10.0].
    game_speed: float = 1.0

    # Per-zone current state
    prices: dict[str, float] = field(default_factory=dict)
    weather: dict[str, WeatherState] = field(default_factory=dict)

    # Pre-coupling "raw" prices (for educational display)
    raw_prices: dict[str, float] = field(default_factory=dict)

    # Interconnector states
    interconnectors: dict[str, InterconnectorState] = field(default_factory=dict)

    # Rolling price history for charts (last 48 ticks = 2 in-game days)
    price_history: dict[str, list[float]] = field(default_factory=dict)

    # Timestamp of last tick (for client sync)
    last_tick_time: float = 0.0

    # ── Weather Forecast System (new) ──
    # 6-hour-ahead forecast per zone. Generated each tick AFTER actual
    # weather is advanced. Each entry is a list of 6 dicts:
    #   [{"hour": h, "wind": float, "solar": float}, ...]
    # Forecast accuracy degrades with horizon (growing Gaussian noise).
    forecasts: dict[str, list[dict]] = field(default_factory=dict)

    # ── Game Events System (new) ──
    # Active events that modify prices, interconnector capacities, etc.
    # Expired events are moved to event_log for the news ticker history.
    active_events: list[GameEvent] = field(default_factory=list)
    event_log: list[dict] = field(default_factory=list)  # Last N events for ticker

    # ── Day-Ahead Market (new) ──
    # Pending bids for the next DA clearing auction.
    # Cleared at DA_CLEARING_HOUR (12:00) each day.
    da_bids: list[DABid] = field(default_factory=list)
    da_clearing_price: float | None = None  # Last cleared DA price
    da_last_results: list[dict] = field(
        default_factory=list
    )  # Results of last clearing
    da_cleared_today: bool = False  # Has the DA auction already run today?


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: MARKET ENGINE — THE CORE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────


def initialize_game_state() -> GameState:
    """Create a fresh game state with initial weather and prices."""
    state = GameState()
    state.last_tick_time = time.time()

    # Initialize weather for each zone
    for zone_id, zone_cfg in ZONES.items():
        state.weather[zone_id] = WeatherState(
            wind_speed=zone_cfg["wind_mean"],
            solar_irradiance=0.0,
            temperature=15.0,
        )
        state.prices[zone_id] = zone_cfg["base_price"]
        state.raw_prices[zone_id] = zone_cfg["base_price"]
        state.price_history[zone_id] = []

    # Initialize interconnectors
    for ic_id, ic_cfg in INTERCONNECTORS.items():
        state.interconnectors[ic_id] = InterconnectorState(
            max_capacity_mw=ic_cfg["max_capacity_mw"],
            zone_a=ic_cfg["zones"][0],
            zone_b=ic_cfg["zones"][1],
        )

    return state


def advance_weather(state: GameState) -> None:
    """
    Update weather for all zones using stochastic processes.

    WIND MODEL — Ornstein-Uhlenbeck Process:
    ──────────────────────────────────────────
    The O-U process is a mean-reverting random walk, perfect for wind:

        dX = θ(μ - X)dt + σ dW

    Where:
        X  = current wind speed
        μ  = long-term mean wind speed (zone-specific)
        θ  = mean-reversion speed (how fast wind returns to average)
        σ  = volatility (zone-specific — Denmark is wild, France is calm)
        dW = Wiener process increment (random normal)

    In discrete form (Euler-Maruyama, dt=1):
        X_new = X + θ(μ - X) + σ * N(0,1)

    This gives us realistic wind behavior:
        - Wind tends to hover near its mean
        - It can drift away temporarily (wind droughts, storms)
        - It always eventually returns to normal

    SOLAR MODEL — Deterministic sinusoidal:
    ────────────────────────────────────────
    Solar irradiance follows the sun's position:
        - Zero at night (hours 20-6)
        - Peaks at solar noon (hour 12-13)
        - Scaled by latitude (northern countries get less)
        - sin(π * (hour - sunrise) / daylight_hours)

    STORM EVENTS:
    ──────────────
    Random wind storms that temporarily double wind speed in a zone.
    These create the dramatic price moves that make the game exciting.
    Probability is higher for coastal zones (DK, NL).
    """
    hour = state.tick
    theta = 0.15  # Mean-reversion speed — moderate pull back to average

    for zone_id, zone_cfg in ZONES.items():
        w = state.weather[zone_id]

        # ── WIND: Ornstein-Uhlenbeck process ──
        mean = zone_cfg["wind_mean"]
        sigma = zone_cfg["wind_volatility"]

        # O-U step: pull toward mean + random shock
        w.wind_speed += theta * (mean - w.wind_speed) + sigma * random.gauss(0, 1)

        # Storm event handling
        if w.storm_active:
            w.storm_ticks_remaining -= 1
            if w.storm_ticks_remaining <= 0:
                w.storm_active = False
            else:
                # During storm: wind speed boosted significantly
                w.wind_speed = max(w.wind_speed, mean * 2.0 + random.gauss(0, 1))
        else:
            # Random storm onset — coastal zones (DK, NL) are more prone
            storm_prob = 0.02  # 2% base chance per hour
            if zone_id in ("DK", "NL"):
                storm_prob = 0.05  # 5% for coastal zones
            if random.random() < storm_prob:
                w.storm_active = True
                w.storm_ticks_remaining = random.randint(3, 8)  # 3-8 hours
                w.wind_speed = mean * 2.5 + random.gauss(0, 2)

        # Clamp wind speed to physically reasonable range
        w.wind_speed = max(0.5, min(25.0, w.wind_speed))

        # ── SOLAR: Deterministic sinusoidal based on hour of day ──
        # Sunrise/sunset approximation (simplified, no seasonal variation)
        sunrise = 6.0
        sunset = 20.0
        daylight_hours = sunset - sunrise

        if sunrise <= hour <= sunset:
            # Solar follows a sine curve peaking at solar noon
            solar_angle = math.pi * (hour - sunrise) / daylight_hours
            # Scale by latitude — higher latitude = less intense sun
            latitude_factor = max(0.3, 1.0 - (zone_cfg["latitude"] - 40.0) / 40.0)
            w.solar_irradiance = math.sin(solar_angle) * latitude_factor
        else:
            w.solar_irradiance = 0.0

        # ── TEMPERATURE: Slow sinusoidal drift ──
        # Peaks at hour 14, troughs at hour 4 (realistic daily cycle)
        w.temperature = 15.0 + 5.0 * math.sin(math.pi * (hour - 4) / 12.0)


def generate_forecasts(state: GameState) -> None:
    """
    Generate 6-hour-ahead weather forecasts for all zones.

    FORECAST MODEL — Deterministic projection + growing Gaussian noise:
    ────────────────────────────────────────────────────────────────────
    Real weather forecasters use numerical weather prediction (NWP) models
    that solve atmospheric physics equations. We simplify this to:

    1. PROJECT the current wind speed forward using the DETERMINISTIC part
       of the Ornstein-Uhlenbeck process (mean-reversion without noise):
           wind_forecast[h] = wind_now + θ * (μ - wind_now) * h

       This gives us the "most likely" future wind — it drifts back toward
       the long-term mean over time.

    2. ADD GROWING NOISE to represent forecast uncertainty:
           wind_forecast[h] += N(0, σ_base * zone_noise_scale * sqrt(h))

       Key insight: forecast error grows with the square root of time.
       This is a well-known property of stochastic processes — predicting
       1 hour ahead is fairly accurate, but 6 hours ahead is much noisier.

    3. STORM FORECASTING — The hardest part in real life:
       - If a storm is currently active: forecast shows elevated wind for
         the remaining storm duration (fairly accurate — storm is obvious)
       - If no storm is active: there's a 70% chance of detecting an
         upcoming storm 2-3 hours before it hits (30% chance of a complete
         "forecast miss" — the market is blindsided)
       - This 30% miss rate is the key educational mechanic: students
         learn that forecasts can fail, and being prepared for surprises
         is what separates good traders from bad ones.

    4. SOLAR FORECASTING — Much easier than wind:
       Solar follows a deterministic sine curve (the sun is predictable!).
       We add only tiny uncertainty for cloud cover: N(0, 0.03 * sqrt(h))
       Solar forecasts are almost always right, which mirrors reality.

    The forecasts are stored in state.forecasts[zone_id] as a list of dicts
    and broadcast to all clients via WebSocket. Players who pay attention
    to forecast-vs-actual divergence gain a significant trading edge.
    """
    hour = state.tick
    theta = 0.15  # Same mean-reversion speed as in advance_weather()

    for zone_id, zone_cfg in ZONES.items():
        w = state.weather[zone_id]
        zone_noise = FORECAST_NOISE_SCALE.get(zone_id, 1.0)
        sigma_base = (
            zone_cfg["wind_volatility"] * 0.6
        )  # Forecast noise is less than actual vol

        forecast_points: list[dict] = []

        for h in range(1, FORECAST_HORIZON + 1):
            future_hour = (hour + h) % 24

            # ── Wind forecast: deterministic O-U projection + noise ──
            # Project forward h steps using only the mean-reversion term
            mean = zone_cfg["wind_mean"]
            projected_wind = w.wind_speed + theta * (mean - w.wind_speed) * h

            # If a storm is active, forecast reflects it for remaining ticks
            if w.storm_active and h <= w.storm_ticks_remaining:
                projected_wind = max(projected_wind, mean * 2.0)

            # Growing Gaussian noise — error increases with sqrt(horizon)
            wind_noise = random.gauss(0, sigma_base * zone_noise * math.sqrt(h))
            forecasted_wind = max(0.5, min(25.0, projected_wind + wind_noise))

            # ── Solar forecast: deterministic sine + tiny cloud uncertainty ──
            sunrise = 6.0
            sunset = 20.0
            daylight_hours = sunset - sunrise

            if sunrise <= future_hour <= sunset:
                solar_angle = math.pi * (future_hour - sunrise) / daylight_hours
                latitude_factor = max(0.3, 1.0 - (zone_cfg["latitude"] - 40.0) / 40.0)
                forecasted_solar = math.sin(solar_angle) * latitude_factor
                # Small cloud uncertainty
                cloud_noise = random.gauss(0, 0.03 * math.sqrt(h))
                forecasted_solar = max(0.0, min(1.0, forecasted_solar + cloud_noise))
            else:
                forecasted_solar = 0.0

            forecast_points.append(
                {
                    "hour": future_hour,
                    "wind": round(forecasted_wind, 1),
                    "solar": round(forecasted_solar, 2),
                }
            )

        state.forecasts[zone_id] = forecast_points


def save_prior_forecasts(state: GameState) -> None:
    """
    Before advancing to the next tick, save what the current forecast
    predicted for the NEXT hour. After the tick advances and actual weather
    is resolved, we compare actual vs this saved prediction to compute
    the 'surprise' component for pricing.

    This is called BEFORE the tick advances, so forecast[0] (which is the
    prediction for tick+1) becomes the "what was expected" for the new tick.
    """
    for zone_id in ZONES:
        forecast = state.forecasts.get(zone_id, [])
        w = state.weather[zone_id]
        if forecast:
            # forecast[0] is the prediction for the NEXT hour
            w.forecasted_wind = forecast[0]["wind"]
            w.forecasted_solar = forecast[0]["solar"]
        else:
            # No forecast yet (first tick) — assume perfect forecast
            w.forecasted_wind = w.wind_speed
            w.forecasted_solar = w.solar_irradiance


def calculate_base_prices(state: GameState) -> None:
    """
    Calculate the "raw" (pre-coupling) price for each zone based on local
    supply and demand conditions, incorporating forecast-vs-actual surprise.

    TWO-LAYER PRICING MODEL:
    ────────────────────────
    In real energy markets, prices are set in TWO stages:

    1. DAY-AHEAD MARKET (forecast-driven):
       The previous day, traders submit bids based on FORECASTED weather.
       If the forecast says "windy tomorrow," wind generators bid low,
       and the cleared price already reflects the expected wind generation.
       → This is the "expected component" — already priced in.

    2. REAL-TIME / INTRADAY MARKET (actual-driven):
       On the day itself, actual weather differs from the forecast.
       If actual wind is HIGHER than forecast → surprise surplus → price drops
       If actual wind is LOWER than forecast → surprise shortage → price spikes
       → This is the "surprise component" — creates trading opportunities.

    THE KEY INSIGHT FOR STUDENTS:
    ─────────────────────────────
    If the forecast says "windy" and it IS windy → price barely moves
        (the wind was already "priced in" by the day-ahead market)
    If the forecast says "calm" but a storm hits → price CRASHES hard
        (surprise surplus, market scrambles to absorb cheap wind power)
    If the forecast says "stormy" but wind dies → price SPIKES hard
        (expected cheap power never materializes, expensive gas fills the gap)

    The SURPRISE_AMPLIFIER (1.5x) means that unexpected weather moves
    prices 50% more than expected weather. This models real market behavior:
    markets overreact to surprises because traders have to adjust positions
    rapidly, creating temporary mispricings that skilled traders can exploit.

    FORMULA:
    ────────
    expected_wind = sensitivity × (forecasted_wind - mean) / mean
    actual_wind   = sensitivity × (actual_wind - mean) / mean
    surprise_wind = (actual_wind - expected_wind) × SURPRISE_AMPLIFIER

    price = base_price
          - expected_wind         [already priced in by market]
          - surprise_wind         [real-time deviation, amplified]
          - expected_solar
          - surprise_solar
          + demand_effect
          + noise
    """
    hour = state.tick
    demand_factor = DUCK_CURVE[hour % 24]

    for zone_id, zone_cfg in ZONES.items():
        w = state.weather[zone_id]
        mean_wind = max(zone_cfg["wind_mean"], 1.0)

        # ── WIND: Split into expected + surprise components ──

        # What the forecast predicted for this hour (saved from prior tick)
        forecast_wind_norm = (w.forecasted_wind - zone_cfg["wind_mean"]) / mean_wind
        # What actually happened
        actual_wind_norm = (w.wind_speed - zone_cfg["wind_mean"]) / mean_wind

        # Expected component — this was already "priced in" by the market
        expected_wind_effect = zone_cfg["wind_sensitivity"] * forecast_wind_norm

        # Surprise component — the deviation, amplified because markets overreact
        surprise_wind_effect = (
            zone_cfg["wind_sensitivity"]
            * (actual_wind_norm - forecast_wind_norm)
            * SURPRISE_AMPLIFIER
        )

        # ── SOLAR: Split into expected + surprise components ──
        expected_solar_effect = zone_cfg["solar_sensitivity"] * w.forecasted_solar
        surprise_solar_effect = (
            zone_cfg["solar_sensitivity"]
            * (w.solar_irradiance - w.forecasted_solar)
            * SURPRISE_AMPLIFIER
        )

        # ── Demand effect (duck curve) — unchanged ──
        # demand_factor ranges from ~0.47 (night) to 1.0 (evening peak)
        # We center it around the midpoint (~0.7) so it can push price up OR down
        demand_deviation = (demand_factor - 0.70) / 0.70
        demand_effect = zone_cfg["demand_elasticity"] * demand_deviation

        # ── Random noise (market microstructure) ──
        noise = random.gauss(0, 2.0)

        # ── Final pre-coupling price ──
        raw_price = (
            zone_cfg["base_price"]
            - expected_wind_effect  # Forecast wind pushes price DOWN (priced in)
            - surprise_wind_effect  # Unexpected wind moves price further (amplified)
            - expected_solar_effect  # Forecast solar pushes price DOWN
            - surprise_solar_effect  # Unexpected solar deviation (amplified)
            + demand_effect  # High demand pushes price UP
            + noise  # Small random component
        )

        state.raw_prices[zone_id] = raw_price
        state.prices[zone_id] = raw_price  # Will be modified by coupling


def apply_market_coupling(state: GameState) -> None:
    """
    Simulate energy flowing between zones via interconnectors.

    MARKET COUPLING ALGORITHM (iterative relaxation):
    ──────────────────────────────────────────────────
    This is a simplified version of how European market coupling works
    (the real EUPHEMIA algorithm is much more complex, but this captures
    the essential price convergence/divergence behavior).

    The idea:
    1. Start with each zone's local (raw) price
    2. For each interconnector, calculate how much power would flow
       from the cheap zone to the expensive zone
    3. CLAMP the flow to the interconnector's physical capacity limit
    4. Adjust both zones' prices based on the actual flow:
       - Exporting zone: price RISES (less surplus)
       - Importing zone: price FALLS (more supply)
    5. Repeat for a few iterations to let the system settle

    KEY EDUCATIONAL INSIGHT:
    ────────────────────────
    When flow is BELOW the capacity limit:
        → Prices CONVERGE between zones (approaching the same level)
        → The interconnector successfully arbitrages the price difference

    When flow HITS the capacity limit (CONGESTION):
        → Prices DIVERGE — they remain different between zones
        → Cheap power is "trapped" in the exporting zone
        → The price gap is called "congestion rent"

    Example scenario students should watch for:
        Denmark wind storm → DK price crashes to -€10/MWh
        Power flows DE←DK at full 2.5 GW (CONGESTED)
        DE price only drops a little (can't import enough cheap Danish power)
        DK stays at -€10, DE stays at €40 → €50 congestion rent per MWh!

    COUPLING FACTOR:
    ────────────────
    The `coupling_factor` determines how much each MW of flow affects the
    price. It's inversely proportional to market size — a small market (DK)
    is more affected by flows than a large market (DE/FR).

        coupling_factor = base_factor / (peak_demand_gw * 1000)

    So 1 GW of flow into tiny Denmark (6 GW peak) moves its price much
    more than 1 GW of flow into large Germany (80 GW peak).
    """
    # Reset prices to raw (pre-coupling) values before iterating
    for zone_id in ZONES:
        state.prices[zone_id] = state.raw_prices[zone_id]

    # Base coupling factor — tuned so that typical flows produce
    # realistic price convergence (~€5-15/MWh typical spread)
    base_coupling_mw_factor = 40.0  # EUR/MWh effect per fraction of market cleared

    # ── Iterative relaxation (3 passes) ──
    # Multiple passes are needed because adjusting one interconnector's
    # flow changes the prices, which affects other interconnectors.
    # 3 passes is enough for our 4-interconnector star topology to converge.
    num_iterations = 3

    for iteration in range(num_iterations):
        for ic_id, ic_cfg in INTERCONNECTORS.items():
            ic_state = state.interconnectors[ic_id]
            zone_a, zone_b = ic_cfg["zones"]

            # Price difference drives flow direction
            # Positive spread → power flows A→B (A is cheaper, exports to B)
            # Negative spread → power flows B→A (B is cheaper, exports to A)
            price_a = state.prices[zone_a]
            price_b = state.prices[zone_b]
            spread = price_b - price_a  # Positive = A is cheaper

            # Desired flow based on price spread and line sensitivity
            # More price difference → more flow (linear response)
            desired_flow_mw = spread * ic_cfg["flow_sensitivity"]

            # ── HARD CAPACITY CONSTRAINT ──
            # This is where congestion happens! If desired flow exceeds
            # the physical line capacity, we clamp it. This is THE key
            # mechanism that creates price divergence between zones.
            max_cap = ic_cfg["max_capacity_mw"]
            actual_flow_mw = max(-max_cap, min(max_cap, desired_flow_mw))

            # Is this line congested? (>= 95% of capacity)
            ic_state.flow_mw = actual_flow_mw
            ic_state.is_congested = abs(actual_flow_mw) >= max_cap * 0.95

            # ── PRICE ADJUSTMENT ──
            # Exporting zone (source of flow): price RISES
            #   → Because we're "removing" cheap supply from the zone
            # Importing zone (sink of flow): price FALLS
            #   → Because we're "adding" supply to the zone
            #
            # Effect is scaled by zone size: same MW flow moves a small
            # market's price much more than a large market's price.
            size_a = ZONES[zone_a]["peak_demand_gw"] * 1000  # Convert GW to MW
            size_b = ZONES[zone_b]["peak_demand_gw"] * 1000

            # Damping factor per iteration (reduce impact on later passes
            # to help convergence — similar to learning rate in optimization)
            damping = 1.0 / (iteration + 1)

            # Price rises in exporting zone, falls in importing zone
            state.prices[zone_a] += (
                (actual_flow_mw / size_a) * base_coupling_mw_factor * damping
            )
            state.prices[zone_b] -= (
                (actual_flow_mw / size_b) * base_coupling_mw_factor * damping
            )

    # ── Floor prices at -50 EUR/MWh ──
    # Negative prices are real (and happen in Denmark during storms),
    # but we cap them at -50 to prevent unrealistic extremes.
    for zone_id in ZONES:
        state.prices[zone_id] = max(-50.0, round(state.prices[zone_id], 2))


def record_price_history(state: GameState) -> None:
    """Append current prices to rolling history buffer (keep last 72 ticks = 3 days)."""
    max_history = 72
    for zone_id in ZONES:
        history = state.price_history[zone_id]
        history.append(state.prices[zone_id])
        if len(history) > max_history:
            state.price_history[zone_id] = history[-max_history:]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6B: GAME EVENTS ENGINE
# ─────────────────────────────────────────────────────────────────────────────
# Random market events that create supply/demand shocks, interconnector faults,
# and policy changes. These add strategic depth beyond weather trading:
#
#   - Players must monitor the news ticker for breaking events
#   - Events create short-term mispricings that skilled traders exploit
#   - Interconnector faults can TRAP cheap power in a zone (educational!)
#   - Carbon price spikes teach about emissions policy impact
#
# Events follow a Poisson-like process: low probability per tick, independent.
# At most 3 events can be active simultaneously to prevent chaos.
# ─────────────────────────────────────────────────────────────────────────────

MAX_ACTIVE_EVENTS = 3  # Prevent too many simultaneous events
MAX_EVENT_LOG = 40  # Keep last 40 events in the ticker


def process_events(state: GameState) -> None:
    """
    Event processing pipeline each tick:
    1. Decrement timers on active events, expire completed ones
    2. Roll for new random events (if under the max)
    3. Apply active event effects to prices and interconnectors
    """
    _expire_events(state)
    _generate_new_events(state)
    _apply_event_effects(state)


def _expire_events(state: GameState) -> None:
    """Decrement tick timers and move expired events to the log."""
    still_active = []
    for event in state.active_events:
        event.ticks_remaining -= 1
        if event.ticks_remaining <= 0:
            # Event expired — add to log with end time
            state.event_log.append(
                {
                    "headline": event.headline,
                    "type": event.event_type,
                    "status": "ENDED",
                    "day": state.day,
                    "tick": state.tick,
                }
            )
            # Trim log
            if len(state.event_log) > MAX_EVENT_LOG:
                state.event_log = state.event_log[-MAX_EVENT_LOG:]
        else:
            still_active.append(event)
    state.active_events = still_active


def _generate_new_events(state: GameState) -> None:
    """
    Roll dice for each event template. If triggered, create an active event.

    To prevent overlapping identical events (e.g., two DE plant outages at
    once), we check that no active event of the same type affects the same
    zone/interconnector.
    """
    if len(state.active_events) >= MAX_ACTIVE_EVENTS:
        return  # Too many active events — skip generation

    # Track what's already active to avoid duplicates
    active_types = set()
    active_zones_by_type = {}
    active_ics = set()
    for ev in state.active_events:
        active_types.add(ev.event_type)
        active_zones_by_type.setdefault(ev.event_type, set()).update(ev.affected_zones)
        if ev.affected_ic:
            active_ics.add(ev.affected_ic)

    for template in EVENT_TEMPLATES:
        if len(state.active_events) >= MAX_ACTIVE_EVENTS:
            break

        # Interconnector fault events are handled differently
        if template["type"] == "interconnector_fault":
            for ic_id in template["interconnectors"]:
                if ic_id in active_ics:
                    continue
                if random.random() < template["probability"]:
                    duration = random.randint(*template["duration_range"])
                    headline = random.choice(template["headlines"]).format(ic=ic_id)
                    event = GameEvent(
                        event_type=template["type"],
                        headline=headline,
                        affected_ic=ic_id,
                        price_mod=0.0,
                        ticks_remaining=duration,
                        started_tick=state.tick,
                        started_day=state.day,
                    )
                    state.active_events.append(event)
                    state.event_log.append(
                        {
                            "headline": headline,
                            "type": template["type"],
                            "status": "BREAKING",
                            "day": state.day,
                            "tick": state.tick,
                        }
                    )
                    if len(state.event_log) > MAX_EVENT_LOG:
                        state.event_log = state.event_log[-MAX_EVENT_LOG:]
                    break  # One IC fault per tick max
            continue

        # Carbon spike is a special "ALL" zone event
        if template.get("zones") == ["ALL"]:
            # Only one carbon event at a time
            if template["type"] in active_types:
                continue
            if random.random() < template["probability"]:
                duration = random.randint(*template["duration_range"])
                headline = random.choice(template["headlines"])
                event = GameEvent(
                    event_type=template["type"],
                    headline=headline,
                    affected_zones=list(ZONES.keys()),
                    price_mod=template["price_mod"],
                    ticks_remaining=duration,
                    started_tick=state.tick,
                    started_day=state.day,
                )
                state.active_events.append(event)
                state.event_log.append(
                    {
                        "headline": headline,
                        "type": template["type"],
                        "status": "BREAKING",
                        "day": state.day,
                        "tick": state.tick,
                    }
                )
                if len(state.event_log) > MAX_EVENT_LOG:
                    state.event_log = state.event_log[-MAX_EVENT_LOG:]
            continue

        # Zone-specific events — pick a random eligible zone
        eligible_zones = [
            z
            for z in template["zones"]
            if z not in active_zones_by_type.get(template["type"], set())
        ]
        if not eligible_zones:
            continue

        for zone in eligible_zones:
            if random.random() < template["probability"]:
                duration = random.randint(*template["duration_range"])
                headline = random.choice(template["headlines"]).format(
                    zone=ZONES[zone]["name"]
                )
                event = GameEvent(
                    event_type=template["type"],
                    headline=headline,
                    affected_zones=[zone],
                    price_mod=template["price_mod"],
                    ticks_remaining=duration,
                    started_tick=state.tick,
                    started_day=state.day,
                )
                state.active_events.append(event)
                state.event_log.append(
                    {
                        "headline": headline,
                        "type": template["type"],
                        "status": "BREAKING",
                        "day": state.day,
                        "tick": state.tick,
                    }
                )
                if len(state.event_log) > MAX_EVENT_LOG:
                    state.event_log = state.event_log[-MAX_EVENT_LOG:]
                break  # One new event per template per tick


def _apply_event_effects(state: GameState) -> None:
    """
    Apply active event effects to the current game state.

    This is called AFTER calculate_base_prices and apply_market_coupling,
    so event effects are additive on top of the weather-driven prices.

    INTERCONNECTOR FAULTS:
    ─────────────────────
    When an IC is faulted, its effective capacity is halved. This is applied
    by temporarily overriding the max_capacity_mw in the interconnector state.
    The coupling algorithm has already run, but we re-adjust the flow to
    respect the reduced capacity and flag congestion.

    PRICE MODIFIERS:
    ────────────────
    Direct price additions/subtractions on affected zones. For carbon spikes,
    the effect is scaled by each zone's carbon intensity (PL gets full hit,
    FR barely notices — reflecting real carbon policy impacts).
    """
    # ── Apply interconnector fault effects ──
    for event in state.active_events:
        if event.event_type == "interconnector_fault" and event.affected_ic:
            ic_id = event.affected_ic
            if ic_id in state.interconnectors:
                ic = state.interconnectors[ic_id]
                # Halve the effective capacity
                original_max = INTERCONNECTORS[ic_id]["max_capacity_mw"]
                reduced_max = original_max * 0.5
                # Clamp current flow to reduced capacity
                ic.flow_mw = max(-reduced_max, min(reduced_max, ic.flow_mw))
                ic.max_capacity_mw = reduced_max
                ic.is_congested = abs(ic.flow_mw) >= reduced_max * 0.95

    # ── Apply price modifier effects ──
    for event in state.active_events:
        if event.price_mod == 0.0:
            continue

        for zone_id in event.affected_zones:
            if zone_id not in state.prices:
                continue

            mod = event.price_mod

            # Carbon events scale by zone carbon intensity
            if event.event_type == "carbon_price_spike":
                mod *= CARBON_INTENSITY.get(zone_id, 0.5)

            state.prices[zone_id] += mod

    # Re-apply floor after event modifications
    for zone_id in ZONES:
        state.prices[zone_id] = max(-50.0, round(state.prices[zone_id], 2))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6C: DAY-AHEAD MARKET CLEARING
# ─────────────────────────────────────────────────────────────────────────────
# The DA market auction runs once per day at DA_CLEARING_HOUR.
#
# HOW DA AUCTIONS WORK IN REAL MARKETS:
# ─────────────────────────────────────
# 1. Before the gate closes, all market participants submit bids:
#      - Generators bid to SELL: "I can produce X MW at ≥ €Y/MWh"
#      - Consumers/traders bid to BUY: "I need X MW at ≤ €Y/MWh"
# 2. EPEX SPOT aggregates all bids into supply and demand curves
# 3. The intersection of the curves sets the CLEARING PRICE
# 4. ALL accepted trades execute at the clearing price (uniform pricing)
#      - This means a nuclear plant bidding €5 still RECEIVES €50 if that's
#        the clearing price. The bidding price is a floor/ceiling, not the
#        execution price. This is the key lesson for students.
#
# OUR SIMPLIFIED VERSION:
# ──────────────────────
# - Clearing price = model-predicted average DE price for next 24h
#   (based on current weather forecast trajectory + base price)
# - Player BUY bids with bid_price >= clearing_price → execute at clearing price
# - Player SELL bids with bid_price <= clearing_price → execute at clearing price
# - Rejected bids are returned (no execution, no cost)
# ─────────────────────────────────────────────────────────────────────────────


def compute_da_clearing_price(state: GameState) -> float:
    """
    Compute the Day-Ahead clearing price by averaging the model's
    forecast of the next 24 hours of DE prices.

    This uses the deterministic O-U projection for wind (no noise — this
    is the "best guess"), the solar sine curve, and the duck curve for
    demand. It's essentially asking: "If forecasts are perfect, what would
    the average price be tomorrow?"

    The result is smooth and predictable — the DA market prices in the
    expected weather trajectory, leaving surprises for the intraday market.
    """
    de_cfg = ZONES["DE"]
    de_weather = state.weather["DE"]
    theta = 0.15

    total_price = 0.0
    for h in range(24):
        future_hour = (state.tick + h) % 24

        # Deterministic wind projection (O-U mean reversion, no noise)
        projected_wind = (
            de_weather.wind_speed
            + theta * (de_cfg["wind_mean"] - de_weather.wind_speed) * h
        )
        projected_wind = max(0.5, min(25.0, projected_wind))

        # Solar (deterministic)
        sunrise, sunset = 6.0, 20.0
        daylight_hours = sunset - sunrise
        if sunrise <= future_hour <= sunset:
            solar_angle = math.pi * (future_hour - sunrise) / daylight_hours
            latitude_factor = max(0.3, 1.0 - (de_cfg["latitude"] - 40.0) / 40.0)
            solar = math.sin(solar_angle) * latitude_factor
        else:
            solar = 0.0

        # Price components
        mean_wind = max(de_cfg["wind_mean"], 1.0)
        wind_norm = (projected_wind - de_cfg["wind_mean"]) / mean_wind
        wind_effect = de_cfg["wind_sensitivity"] * wind_norm
        solar_effect = de_cfg["solar_sensitivity"] * solar

        demand_factor = DUCK_CURVE[future_hour]
        demand_deviation = (demand_factor - 0.70) / 0.70
        demand_effect = de_cfg["demand_elasticity"] * demand_deviation

        hour_price = de_cfg["base_price"] - wind_effect - solar_effect + demand_effect
        total_price += hour_price

    return round(total_price / 24.0, 2)


async def clear_da_auction(state: GameState) -> None:
    """
    Execute the Day-Ahead auction: determine clearing price and settle bids.

    CLEARING RULES (pay-as-cleared / uniform pricing):
    ──────────────────────────────────────────────────
    1. Compute clearing price from the forecast model
    2. BUY bids: if bid_price >= clearing_price → ACCEPTED
       (Player was willing to pay at least the clearing price)
    3. SELL bids: if bid_price <= clearing_price → ACCEPTED
       (Player was willing to accept at most the clearing price)
    4. ALL accepted trades execute at the clearing_price
    5. Rejected bids get a notification (their limit was too tight)

    Note: We don't enforce that buy volume = sell volume since the "market"
    (the grid) is the counterparty. This is a simplification — in real
    markets, unmatched volume would remain uncleared.
    """
    global db

    clearing_price = compute_da_clearing_price(state)
    state.da_clearing_price = clearing_price
    state.da_last_results = []

    for bid in state.da_bids:
        accepted = False
        if bid.action == "BUY" and bid.bid_price >= clearing_price:
            accepted = True
        elif bid.action == "SELL" and bid.bid_price <= clearing_price:
            accepted = True

        result = {
            "player_name": bid.player_name,
            "action": bid.action,
            "mw": bid.mw,
            "bid_price": bid.bid_price,
            "clearing_price": clearing_price,
            "accepted": accepted,
        }

        if accepted and db:
            # Fetch player current state (including battery physics columns)
            async with db.execute(
                "SELECT id, cash, battery_mwh, cumulative_pnl, battery_soh, battery_cycles, soh_history FROM players WHERE id = ?",
                (bid.player_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                p_id, p_cash, p_battery, p_pnl = row[0], row[1], row[2], row[3]
                p_soh = row[4] if row[4] is not None else BATTERY_START_SOH
                p_cycles = row[5] if row[5] is not None else 0.0
                p_soh_hist_raw = row[6] if row[6] is not None else "[]"
                try:
                    p_soh_history = json.loads(p_soh_hist_raw)
                except (json.JSONDecodeError, TypeError):
                    p_soh_history = []

                # Build a player dict for the shared physics helper
                player_dict = {
                    "id": p_id,
                    "cash": p_cash,
                    "battery_mwh": p_battery,
                    "pnl": p_pnl,
                    "battery_soh": p_soh,
                    "battery_cycles": p_cycles,
                    "soh_history": p_soh_history,
                }

                mw = bid.mw
                # Apply battery physics via shared helper
                trade_result = execute_battery_trade(
                    bid.action, mw, player_dict, clearing_price
                )

                if "error" in trade_result:
                    result["accepted"] = False
                    result["note"] = trade_result["error"]
                    state.da_last_results.append(result)
                    continue

                new_cash = trade_result["new_cash"]
                new_battery = trade_result["new_battery"]
                new_soh = trade_result["new_soh"]
                new_cycles = trade_result["new_cycles"]
                traded_mw = trade_result["mw"]
                pnl_delta = trade_result["pnl_delta"]
                total_cost = trade_result["total_cost"]
                new_pnl = p_pnl + pnl_delta

                # Update SoH history
                p_soh_history.append(
                    {
                        "tick": state.tick,
                        "day": state.day,
                        "soh": round(new_soh, 4),
                        "cycles": round(new_cycles, 2),
                        "rt_eff": round(trade_result["rt_efficiency"], 4),
                    }
                )
                if len(p_soh_history) > 500:
                    p_soh_history = p_soh_history[-500:]

                await db.execute(
                    "UPDATE players SET cash = ?, battery_mwh = ?, cumulative_pnl = ?, battery_soh = ?, battery_cycles = ?, soh_history = ? WHERE id = ?",
                    (
                        new_cash,
                        new_battery,
                        new_pnl,
                        new_soh,
                        new_cycles,
                        json.dumps(p_soh_history),
                        p_id,
                    ),
                )
                await db.execute(
                    "INSERT INTO trades (player_id, tick, day, action, mw, price_eur_mwh, total_cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        p_id,
                        state.tick,
                        state.day,
                        f"DA_{bid.action}",  # Prefix with DA_ to distinguish from intraday
                        traded_mw,
                        clearing_price,
                        total_cost,
                        time.time(),
                    ),
                )

                result["executed_mw"] = traded_mw
                result["executed_price"] = clearing_price

        state.da_last_results.append(result)

    if db:
        await db.commit()

    # Clear the bid book
    state.da_bids = []
    state.da_cleared_today = True

    # Add to event log for news ticker
    n_accepted = sum(1 for r in state.da_last_results if r.get("accepted"))
    n_total = len(state.da_last_results)
    if n_total > 0:
        state.event_log.append(
            {
                "headline": f"DA AUCTION CLEARED @ \u20ac{clearing_price:.1f}/MWh — {n_accepted}/{n_total} bids accepted",
                "type": "da_clearing",
                "status": "BREAKING",
                "day": state.day,
                "tick": state.tick,
            }
        )
        if len(state.event_log) > MAX_EVENT_LOG:
            state.event_log = state.event_log[-MAX_EVENT_LOG:]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: FASTAPI APPLICATION & DATABASE
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Battery Trader Sim", version="2.0.0")

# Global game state — lives in memory, shared across all requests
game_state: GameState = initialize_game_state()

# Connected WebSocket clients — maps token → WebSocket
ws_clients: dict[str, WebSocket] = {}

# Database connection (initialized on startup)
db: aiosqlite.Connection | None = None

# ── Rate Limiting ──
# Per-token sliding window of recent trade timestamps.
_trade_timestamps: dict[str, list[float]] = {}


def check_rate_limit(token: str) -> bool:
    """Return True if the trade is allowed, False if rate-limited."""
    now = time.time()
    timestamps = _trade_timestamps.get(token, [])
    # Trim timestamps outside the window
    cutoff = now - TRADE_RATE_WINDOW
    timestamps = [t for t in timestamps if t > cutoff]
    if len(timestamps) >= TRADE_RATE_LIMIT:
        _trade_timestamps[token] = timestamps
        return False
    timestamps.append(now)
    _trade_timestamps[token] = timestamps
    return True


async def init_db() -> aiosqlite.Connection:
    """
    Initialize SQLite database with WAL mode for concurrent reads.
    Creates tables if they don't exist. Migrates schema if needed.
    """
    conn = await aiosqlite.connect(DB_PATH)
    # WAL mode allows concurrent readers while writing — essential for
    # a game where we read the leaderboard while processing trades.
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")

    # Players table — one row per registered student
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL,
            cash REAL NOT NULL DEFAULT 0.0,
            battery_mwh REAL NOT NULL DEFAULT 25000.0,
            cumulative_pnl REAL NOT NULL DEFAULT 0.0,
            created_at REAL NOT NULL
        )
    """)

    # ── Schema migration for trades table ──
    # If the trades table exists with the old CHECK constraint (only BUY/SELL),
    # we need to recreate it with the updated constraint that allows DA_BUY/DA_SELL.
    # SQLite doesn't support ALTER TABLE to change constraints, so we recreate.
    try:
        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
        ) as cursor:
            row = await cursor.fetchone()
        if row and row[0] and "DA_BUY" not in row[0]:
            # Old schema — migrate
            print("[DB MIGRATION] Recreating trades table with DA_BUY/DA_SELL support")
            await conn.execute("ALTER TABLE trades RENAME TO trades_old")
            await conn.execute("""
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id TEXT NOT NULL REFERENCES players(id),
                    tick INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'DA_BUY', 'DA_SELL')),
                    mw REAL NOT NULL,
                    price_eur_mwh REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            await conn.execute("INSERT INTO trades SELECT * FROM trades_old")
            await conn.execute("DROP TABLE trades_old")
            await conn.commit()
            print("[DB MIGRATION] Done — trade history preserved")
    except Exception as e:
        print(f"[DB MIGRATION WARNING] {e}")

    # ── Schema migration for battery physics columns ──
    # Add battery_soh, battery_cycles, and soh_history columns to players table.
    # These support the new realistic battery degradation model.
    try:
        async with conn.execute("PRAGMA table_info(players)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "battery_soh" not in columns:
            print("[DB MIGRATION] Adding battery physics columns to players table")
            await conn.execute(
                "ALTER TABLE players ADD COLUMN battery_soh REAL NOT NULL DEFAULT 1.0"
            )
            await conn.execute(
                "ALTER TABLE players ADD COLUMN battery_cycles REAL NOT NULL DEFAULT 0.0"
            )
            await conn.execute(
                "ALTER TABLE players ADD COLUMN soh_history TEXT NOT NULL DEFAULT '[]'"
            )
            await conn.commit()
            print("[DB MIGRATION] Battery physics columns added successfully")
    except Exception as e:
        print(f"[DB MIGRATION WARNING] battery physics columns: {e}")

    # Trades table — full audit log of every trade
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id TEXT NOT NULL REFERENCES players(id),
            tick INTEGER NOT NULL,
            day INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL', 'DA_BUY', 'DA_SELL')),
            mw REAL NOT NULL,
            price_eur_mwh REAL NOT NULL,
            total_cost REAL NOT NULL,
            created_at REAL NOT NULL
        )
    """)

    # Price history table — for persistence across restarts (optional)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tick INTEGER NOT NULL,
            day INTEGER NOT NULL,
            zone TEXT NOT NULL,
            price_eur_mwh REAL NOT NULL
        )
    """)

    # Game sessions table — for save/load/autosave
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS game_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            state_json TEXT NOT NULL,
            tick INTEGER NOT NULL DEFAULT 0,
            day INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        )
    """)

    await conn.commit()
    return conn


# ── Game State Serialization (for save/load/autosave) ──


def serialize_game_state(state: GameState) -> str:
    """Serialize the in-memory GameState to a JSON string for persistence."""
    data = {
        "tick": state.tick,
        "day": state.day,
        "game_speed": state.game_speed,
        "prices": state.prices,
        "raw_prices": state.raw_prices,
        "last_tick_time": state.last_tick_time,
        "da_clearing_price": state.da_clearing_price,
        "da_cleared_today": state.da_cleared_today,
        "da_last_results": state.da_last_results,
        "event_log": state.event_log[-40:],
        "weather": {
            zone_id: {
                "wind_speed": ws.wind_speed,
                "solar_irradiance": ws.solar_irradiance,
                "temperature": ws.temperature,
                "storm_active": ws.storm_active,
                "storm_ticks_remaining": ws.storm_ticks_remaining,
                "forecasted_wind": ws.forecasted_wind,
                "forecasted_solar": ws.forecasted_solar,
            }
            for zone_id, ws in state.weather.items()
        },
        "interconnectors": {
            ic_id: {
                "flow_mw": ic.flow_mw,
                "max_capacity_mw": ic.max_capacity_mw,
                "is_congested": ic.is_congested,
                "zone_a": ic.zone_a,
                "zone_b": ic.zone_b,
            }
            for ic_id, ic in state.interconnectors.items()
        },
        "price_history": state.price_history,
        "forecasts": state.forecasts,
        "active_events": [
            {
                "event_type": e.event_type,
                "headline": e.headline,
                "affected_zones": e.affected_zones,
                "affected_ic": e.affected_ic,
                "price_mod": e.price_mod,
                "ticks_remaining": e.ticks_remaining,
                "started_tick": e.started_tick,
                "started_day": e.started_day,
            }
            for e in state.active_events
        ],
        "da_bids": [
            {
                "player_id": b.player_id,
                "player_name": b.player_name,
                "action": b.action,
                "mw": b.mw,
                "bid_price": b.bid_price,
                "submitted_tick": b.submitted_tick,
                "submitted_day": b.submitted_day,
            }
            for b in state.da_bids
        ],
    }
    return json.dumps(data)


def deserialize_game_state(data_str: str) -> GameState:
    """Restore a GameState from a JSON string."""
    data = json.loads(data_str)
    state = GameState()
    state.tick = data.get("tick", 0)
    state.day = data.get("day", 1)
    state.game_speed = data.get("game_speed", 1.0)
    state.prices = data.get("prices", {})
    state.raw_prices = data.get("raw_prices", {})
    state.last_tick_time = data.get("last_tick_time", time.time())
    state.da_clearing_price = data.get("da_clearing_price")
    state.da_cleared_today = data.get("da_cleared_today", False)
    state.da_last_results = data.get("da_last_results", [])
    state.event_log = data.get("event_log", [])
    state.price_history = data.get("price_history", {})
    state.forecasts = data.get("forecasts", {})

    # Restore weather
    for zone_id, ws_data in data.get("weather", {}).items():
        state.weather[zone_id] = WeatherState(
            wind_speed=ws_data["wind_speed"],
            solar_irradiance=ws_data["solar_irradiance"],
            temperature=ws_data["temperature"],
            storm_active=ws_data.get("storm_active", False),
            storm_ticks_remaining=ws_data.get("storm_ticks_remaining", 0),
            forecasted_wind=ws_data.get("forecasted_wind", ws_data["wind_speed"]),
            forecasted_solar=ws_data.get(
                "forecasted_solar", ws_data["solar_irradiance"]
            ),
        )

    # Restore interconnectors
    for ic_id, ic_data in data.get("interconnectors", {}).items():
        state.interconnectors[ic_id] = InterconnectorState(
            flow_mw=ic_data["flow_mw"],
            max_capacity_mw=ic_data["max_capacity_mw"],
            is_congested=ic_data.get("is_congested", False),
            zone_a=ic_data["zone_a"],
            zone_b=ic_data["zone_b"],
        )

    # Restore active events
    for e_data in data.get("active_events", []):
        state.active_events.append(
            GameEvent(
                event_type=e_data["event_type"],
                headline=e_data["headline"],
                affected_zones=e_data.get("affected_zones", []),
                affected_ic=e_data.get("affected_ic"),
                price_mod=e_data.get("price_mod", 0.0),
                ticks_remaining=e_data.get("ticks_remaining", 0),
                started_tick=e_data.get("started_tick", 0),
                started_day=e_data.get("started_day", 0),
            )
        )

    # Restore DA bids
    for b_data in data.get("da_bids", []):
        state.da_bids.append(
            DABid(
                player_id=b_data["player_id"],
                player_name=b_data["player_name"],
                action=b_data["action"],
                mw=b_data["mw"],
                bid_price=b_data["bid_price"],
                submitted_tick=b_data.get("submitted_tick", 0),
                submitted_day=b_data.get("submitted_day", 0),
            )
        )

    # Ensure all zones/ICs are present (in case save was from older version)
    for zone_id, zone_cfg in ZONES.items():
        if zone_id not in state.weather:
            state.weather[zone_id] = WeatherState(wind_speed=zone_cfg["wind_mean"])
        if zone_id not in state.prices:
            state.prices[zone_id] = zone_cfg["base_price"]
        if zone_id not in state.raw_prices:
            state.raw_prices[zone_id] = zone_cfg["base_price"]
        if zone_id not in state.price_history:
            state.price_history[zone_id] = []
    for ic_id, ic_cfg in INTERCONNECTORS.items():
        if ic_id not in state.interconnectors:
            state.interconnectors[ic_id] = InterconnectorState(
                max_capacity_mw=ic_cfg["max_capacity_mw"],
                zone_a=ic_cfg["zones"][0],
                zone_b=ic_cfg["zones"][1],
            )

    return state


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: BACKGROUND GAME LOOP
# ─────────────────────────────────────────────────────────────────────────────


async def game_loop():
    """
    The heartbeat of the simulation. Runs every TICK_INTERVAL_SECONDS (1s).
    Each tick = 1 in-game hour. Sequence:

    1. Save prior forecasts (what was predicted for THIS hour)
    2. Advance weather (wind, solar, storms)
    3. Generate new forecasts (6 hours ahead with growing noise)
    4. Calculate base prices (two-layer: expected + surprise)
    5. Apply market coupling (interconnector flows)
    6. Process game events (generate new events, apply effects, expire old)
    7. Record price history (AFTER events so chart reflects event impact)
    8. Persist to database
    9. Broadcast state to all connected WebSocket clients
    """
    global game_state, db

    while game_state.running:
        # ── Pause check: spin-wait while paused ──
        while game_state.paused and game_state.running:
            await asyncio.sleep(0.5)

        if not game_state.running:
            break

        try:
            # ── Step 1: Save what was forecasted for THIS hour ──
            save_prior_forecasts(game_state)

            # ── Step 2: Weather (actual stochastic evolution) ──
            advance_weather(game_state)

            # ── Step 3: Generate new forecasts (6h ahead) ──
            generate_forecasts(game_state)

            # ── Step 4: Base prices (forecast + surprise) ──
            calculate_base_prices(game_state)

            # ── Step 4.5: Restore IC capacities before coupling ──
            # Event effects may have reduced IC capacity on the prior tick.
            # We restore to full capacity here so coupling runs with the
            # real physical limits, then _apply_event_effects will re-reduce.
            for ic_id, ic_cfg in INTERCONNECTORS.items():
                game_state.interconnectors[ic_id].max_capacity_mw = ic_cfg[
                    "max_capacity_mw"
                ]

            # ── Step 5: Market coupling ──
            apply_market_coupling(game_state)

            # ── Step 6: Game events (new!) ──
            # Process AFTER coupling so events layer on top of the
            # weather-driven price. IC faults retroactively constrain flows.
            process_events(game_state)

            # ── Step 6.5: Day-Ahead market clearing (new!) ──
            # At DA_CLEARING_HOUR, clear the DA auction if it hasn't run yet today
            if game_state.tick == DA_CLEARING_HOUR and not game_state.da_cleared_today:
                await clear_da_auction(game_state)

            # ── Step 7: Record history ──
            record_price_history(game_state)

            # ── Step 8: Persist price snapshot to DB ──
            if db:
                for zone_id, price in game_state.prices.items():
                    await db.execute(
                        "INSERT INTO price_history (tick, day, zone, price_eur_mwh) VALUES (?, ?, ?, ?)",
                        (game_state.tick, game_state.day, zone_id, price),
                    )

                # ── Step 8.5: Battery self-discharge ──
                # Every tick (hour), all batteries lose a tiny fraction of stored
                # energy.  Real Li-ion self-discharge is ~2-5% per month; we model
                # 0.02% per hour (~0.5%/day) which is slightly accelerated for
                # gameplay visibility.
                await db.execute(
                    "UPDATE players SET battery_mwh = battery_mwh * ?",
                    (1.0 - BATTERY_SELF_DISCHARGE_RATE,),
                )

                await db.commit()

            # ── Step 9: Broadcast to all WebSocket clients ──
            await broadcast_state()

            # ── Advance clock ──
            game_state.last_tick_time = time.time()
            game_state.tick = (game_state.tick + 1) % 24
            if game_state.tick == 0:
                game_state.day += 1
                game_state.da_cleared_today = False  # Reset DA flag for new day

                # ── Autosave every new day ──
                if db:
                    try:
                        state_json = serialize_game_state(game_state)
                        await db.execute(
                            "INSERT OR REPLACE INTO game_sessions (name, state_json, tick, day, created_at) VALUES (?, ?, ?, ?, ?)",
                            (
                                "autosave",
                                state_json,
                                game_state.tick,
                                game_state.day,
                                time.time(),
                            ),
                        )
                        await db.commit()
                    except Exception as save_err:
                        print(f"[AUTOSAVE ERROR] {save_err}")

        except Exception as e:
            # Don't let errors crash the game loop
            print(f"[GAME LOOP ERROR] {e}")

        await asyncio.sleep(TICK_INTERVAL_SECONDS * game_state.game_speed)


async def broadcast_state():
    """Send current game state to all connected WebSocket clients."""
    if not ws_clients:
        return

    # Build leaderboard from DB with realized + floating PnL breakdown
    leaderboard = []
    if db:
        de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
        starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
        # Realized PnL = cash (net cash from all trades, starts at 0)
        # Floating PnL = battery_mwh * DE_price - starting_value (paper gain/loss on inventory)
        # Ranked by realized PnL (stable, rewards locked-in profits)
        async with db.execute(
            """SELECT username, cash, battery_mwh,
                      cash AS realized_pnl,
                      (battery_mwh * ? - ?) AS floating_pnl
               FROM players
               ORDER BY realized_pnl DESC LIMIT 20""",
            (de_price, starting_value),
        ) as cursor:
            rows = await cursor.fetchall()
            for rank, row in enumerate(rows, 1):
                leaderboard.append(
                    {
                        "rank": rank,
                        "username": row[0],
                        "realized_pnl": round(row[3], 2),
                        "floating_pnl": round(row[4], 2),
                        "pnl": round(
                            row[3] + row[4], 2
                        ),  # Total MTM PnL (for backwards compat)
                        "battery_mwh": round(row[2], 1),
                        "cash": round(row[1], 2),
                    }
                )

    # Serialize game state for WebSocket push
    state_payload = build_state_payload(leaderboard)
    message = json.dumps(state_payload)

    # Send to all clients, removing disconnected ones
    disconnected = []
    for token, ws in ws_clients.items():
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(token)

    for token in disconnected:
        ws_clients.pop(token, None)


def build_state_payload(leaderboard: list[dict]) -> dict:
    """Serialize the full game state into a JSON-friendly dict."""
    state = game_state

    return {
        "tick": state.tick,
        "day": state.day,
        "timestamp": state.last_tick_time,
        "game_speed": state.game_speed,
        "prices": {z: round(p, 2) for z, p in state.prices.items()},
        "raw_prices": {z: round(p, 2) for z, p in state.raw_prices.items()},
        "weather": {
            zone_id: {
                "wind_speed": round(w.wind_speed, 1),
                "solar_irradiance": round(w.solar_irradiance, 2),
                "temperature": round(w.temperature, 1),
                "storm_active": w.storm_active,
                # Forecast-vs-actual comparison for this hour
                "forecasted_wind": round(w.forecasted_wind, 1),
                "forecasted_solar": round(w.forecasted_solar, 2),
            }
            for zone_id, w in state.weather.items()
        },
        "interconnectors": {
            ic_id: {
                "zone_a": ic.zone_a,
                "zone_b": ic.zone_b,
                "flow_mw": round(ic.flow_mw, 0),
                "max_capacity_mw": ic.max_capacity_mw,
                "is_congested": ic.is_congested,
            }
            for ic_id, ic in state.interconnectors.items()
        },
        "price_history": {
            zone_id: [round(p, 2) for p in prices]
            for zone_id, prices in state.price_history.items()
        },
        # 6-hour-ahead weather forecast per zone
        "forecasts": state.forecasts,
        "leaderboard": leaderboard,
        # ── Game events (new) ──
        "active_events": [
            {
                "type": ev.event_type,
                "headline": ev.headline,
                "zones": ev.affected_zones,
                "ic": ev.affected_ic,
                "ticks_remaining": ev.ticks_remaining,
            }
            for ev in state.active_events
        ],
        "event_log": state.event_log[-20:],  # Last 20 for news ticker
        # ── Day-Ahead market (new) ──
        "da_market": {
            "clearing_price": state.da_clearing_price,
            "num_pending_bids": len(state.da_bids),
            "cleared_today": state.da_cleared_today,
            "clearing_hour": DA_CLEARING_HOUR,
            "last_results": state.da_last_results[-10:]
            if state.da_last_results
            else [],
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9: STARTUP & SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Initialize database, restore autosave if available, and start the background game loop."""
    global db, game_state
    db = await init_db()

    # ── Try to restore from autosave ──
    try:
        async with db.execute(
            "SELECT state_json, day, tick FROM game_sessions WHERE name = 'autosave'"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                game_state = deserialize_game_state(row[0])
                game_state.running = True
                game_state.paused = False
                print(
                    f"  Restored game from autosave (Day {game_state.day}, Hour {game_state.tick})"
                )
    except Exception as e:
        print(f"  [AUTOSAVE RESTORE] Could not restore: {e} — starting fresh")

    asyncio.create_task(game_loop())
    print("=" * 60)
    print("  BATTERY TRADER SIM — Server Started")
    print(f"  Open http://localhost:8000 to play")
    print(f"  Class password: {CLASS_PASSWORD}")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown():
    """Clean shutdown: autosave, stop game loop, close database."""
    global db
    game_state.running = False

    # ── Final autosave on shutdown ──
    if db:
        try:
            state_json = serialize_game_state(game_state)
            await db.execute(
                "INSERT OR REPLACE INTO game_sessions (name, state_json, tick, day, created_at) VALUES (?, ?, ?, ?, ?)",
                ("autosave", state_json, game_state.tick, game_state.day, time.time()),
            )
            await db.commit()
            print("[SHUTDOWN] Game state autosaved")
        except Exception as e:
            print(f"[SHUTDOWN] Autosave failed: {e}")
        await db.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10: REST API ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the single-page frontend."""
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/register")
async def register(request: Request):
    """
    Register a new player, or log in if the username already exists.
    Requires:
        { "username": "alice", "password": "trade2026" }
    Returns:
        { "token": "uuid-...", "player_id": "uuid-..." }
    """
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username:
        raise HTTPException(400, "Username is required")
    if len(username) > 20:
        raise HTTPException(400, "Username must be 20 characters or less")
    if password != CLASS_PASSWORD:
        raise HTTPException(403, "Incorrect class password")

    player_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    try:
        await db.execute(
            "INSERT INTO players (id, username, token, cash, battery_mwh, cumulative_pnl, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                player_id,
                username,
                token,
                STARTING_CASH,
                BATTERY_START_MWH,
                0.0,
                time.time(),
            ),
        )
        await db.commit()
        return {"token": token, "player_id": player_id, "username": username}
    except aiosqlite.IntegrityError:
        # Username already exists — fall through to login
        async with db.execute(
            "SELECT id, token, cash, battery_mwh FROM players WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(500, "Unexpected error — please try again")
        cash = row[2]
        battery_mwh = row[3]
        return {
            "player_id": row[0],
            "token": row[1],
            "username": username,
            "cash": round(cash, 2),
            "battery_mwh": round(battery_mwh, 1),
            "pnl": round(compute_mtm_pnl(cash, battery_mwh), 2),
        }


@app.post("/api/login")
async def login(request: Request):
    """
    Login an existing player, or auto-register if the username is new.
    Requires:
        { "username": "alice", "password": "trade2026" }
    Returns the player's existing token (or a new one if auto-registered).
    """
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username:
        raise HTTPException(400, "Username is required")
    if len(username) > 20:
        raise HTTPException(400, "Username must be 20 characters or less")
    if password != CLASS_PASSWORD:
        raise HTTPException(403, "Incorrect class password")

    async with db.execute(
        "SELECT id, token, cash, battery_mwh, cumulative_pnl, battery_soh, battery_cycles, soh_history FROM players WHERE username = ?",
        (username,),
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        # Existing player — return their data
        cash = row[2]
        battery_mwh = row[3]
        soh = row[5] if row[5] is not None else BATTERY_START_SOH
        cycles = row[6] if row[6] is not None else 0.0
        soh_history_raw = row[7] if row[7] is not None else "[]"
        try:
            soh_history = json.loads(soh_history_raw)
        except (json.JSONDecodeError, TypeError):
            soh_history = []
        return {
            "player_id": row[0],
            "token": row[1],
            "username": username,
            "cash": round(cash, 2),
            "battery_mwh": round(battery_mwh, 1),
            "pnl": round(compute_mtm_pnl(cash, battery_mwh), 2),
            "battery_soh": round(soh, 4),
            "battery_cycles": round(cycles, 2),
            "soh_history": soh_history,
        }

    # New player — auto-register
    player_id = str(uuid.uuid4())
    token = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO players (id, username, token, cash, battery_mwh, cumulative_pnl, created_at, battery_soh, battery_cycles, soh_history) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            player_id,
            username,
            token,
            STARTING_CASH,
            BATTERY_START_MWH,
            0.0,
            time.time(),
            BATTERY_START_SOH,
            0.0,
            "[]",
        ),
    )
    await db.commit()
    return {"token": token, "player_id": player_id, "username": username}


def compute_mtm_pnl(cash: float, battery_mwh: float) -> float:
    """
    Compute mark-to-market PnL for a player.

    MARK-TO-MARKET VALUATION:
    ─────────────────────────
    In real trading, PnL has two components:
      1. Realized PnL: cash profit/loss from completed buy→sell cycles
      2. Unrealized PnL: paper gain/loss on inventory still held

    Total MTM PnL = Realized + Unrealized
        = cash + (battery_mwh × current_DE_price) - (BATTERY_START_MWH × STARTING_REF_PRICE)

    At game start:  cash=0, battery=25000, DE_price≈45 → MTM = 0 + 25000*45 - 25000*45 = 0 ✓
    After buying:   cash goes negative, battery goes up → MTM depends on price move
    After selling:  cash goes positive, battery goes down → realized gains locked in

    This means the leaderboard ranking changes EVERY TICK even if nobody trades —
    because the current DE price moves, so battery values change. This is realistic:
    real traders' PnL fluctuates with mark-to-market accounting, even overnight.
    """
    de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
    current_portfolio_value = cash + battery_mwh * de_price
    starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
    return current_portfolio_value - starting_value


def compute_pnl_breakdown(cash: float, battery_mwh: float) -> dict:
    """
    Decompose PnL into realized and floating components.

    PnL BREAKDOWN:
    ───────────────
    Realized PnL  = cash balance (starts at 0; increases on sells, decreases on buys).
                    This is the "locked-in" profit from completed trades — it only
                    changes when the player executes a trade, never from price moves.

    Floating PnL  = (battery_mwh × current_DE_price) - (BATTERY_START_MWH × STARTING_REF_PRICE)
                    This is the paper gain/loss on the battery inventory relative to
                    its starting value. It fluctuates every tick with the market price.

    Total MTM PnL = Realized + Floating  (same as compute_mtm_pnl)

    At game start:  realized=0, floating=25000*45 - 25000*45 = 0  ✓
    After buying:   realized goes negative (cash spent), floating goes up (more MWh)
    After selling:  realized goes positive (cash received), floating goes down (less MWh)
    Price moves:    realized unchanged, floating changes with DE price
    """
    de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
    starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
    realized = cash
    floating = battery_mwh * de_price - starting_value
    return {
        "realized_pnl": realized,
        "floating_pnl": floating,
        "total_pnl": realized + floating,
    }


async def get_player_by_token(token: str) -> dict | None:
    """Helper: look up a player by their auth token."""
    async with db.execute(
        "SELECT id, username, cash, battery_mwh, cumulative_pnl, battery_soh, battery_cycles, soh_history FROM players WHERE token = ?",
        (token,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    cash = row[2]
    battery_mwh = row[3]
    soh = row[5] if row[5] is not None else BATTERY_START_SOH
    cycles = row[6] if row[6] is not None else 0.0
    soh_history_raw = row[7] if row[7] is not None else "[]"
    try:
        soh_history = json.loads(soh_history_raw)
    except (json.JSONDecodeError, TypeError):
        soh_history = []
    return {
        "id": row[0],
        "username": row[1],
        "cash": cash,
        "battery_mwh": battery_mwh,
        "pnl": row[4],  # Realized PnL (kept for reference)
        "mtm_pnl": compute_mtm_pnl(cash, battery_mwh),  # Mark-to-market PnL
        "battery_soh": soh,
        "battery_cycles": cycles,
        "soh_history": soh_history,
    }


async def get_player_analytics(player_id: str) -> dict:
    """
    Compute portfolio analytics from the trades table for a given player.

    Returns weighted average buy/sell prices, total volumes, trade count,
    and the last 10 trades for the trade log display.

    PORTFOLIO METRICS EXPLAINED:
    ────────────────────────────
    Weighted Average Buy Price (WABP):
        WABP = Σ(mw_i × price_i) / Σ(mw_i)  for all BUY trades
        This is the average price the player has paid per MWh of stored energy.
        If current DE price > WABP → selling is profitable (sell high, bought low)
        If current DE price < WABP → selling locks in a loss (player is "underwater")

    Weighted Average Sell Price (WASP):
        Same formula but for SELL trades.
        WASP - WABP = average spread per MWh = the player's trading skill metric.

    These metrics help students answer the key question:
        "Is NOW a good time to buy or sell?"
    By comparing the current DE price against their personal averages.
    """
    analytics = {
        "avg_buy_price": 0.0,
        "avg_sell_price": 0.0,
        "total_bought_mwh": 0.0,
        "total_sold_mwh": 0.0,
        "buy_count": 0,
        "sell_count": 0,
        "recent_trades": [],
    }

    # Weighted average buy price (includes both intraday BUY and DA_BUY)
    async with db.execute(
        "SELECT SUM(mw * price_eur_mwh), SUM(mw), COUNT(*) FROM trades WHERE player_id = ? AND action IN ('BUY', 'DA_BUY')",
        (player_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row and row[1] and row[1] > 0:
        analytics["avg_buy_price"] = round(row[0] / row[1], 2)
        analytics["total_bought_mwh"] = round(row[1], 1)
        analytics["buy_count"] = row[2]

    # Weighted average sell price (includes both intraday SELL and DA_SELL)
    async with db.execute(
        "SELECT SUM(mw * price_eur_mwh), SUM(mw), COUNT(*) FROM trades WHERE player_id = ? AND action IN ('SELL', 'DA_SELL')",
        (player_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row and row[1] and row[1] > 0:
        analytics["avg_sell_price"] = round(row[0] / row[1], 2)
        analytics["total_sold_mwh"] = round(row[1], 1)
        analytics["sell_count"] = row[2]

    # Last 10 trades
    async with db.execute(
        "SELECT action, mw, price_eur_mwh, tick, day FROM trades WHERE player_id = ? ORDER BY created_at DESC LIMIT 10",
        (player_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    analytics["recent_trades"] = [
        {
            "action": row[0],
            "mw": round(row[1], 0),
            "price": round(row[2], 2),
            "tick": row[3],
            "day": row[4],
        }
        for row in rows
    ]

    return analytics


# ─────────────────────────────────────────────────────────────────────────────
# BATTERY PHYSICS HELPERS
# ─────────────────────────────────────────────────────────────────────────────
# These functions encapsulate the realistic battery model so both the REST
# and WebSocket trade paths use identical physics.  Key physical constraints:
#
#   1. Round-trip efficiency   — energy is lost on both charge and discharge
#   2. SoC-dependent taper    — charge rate drops above 80% SoC
#   3. Cycle-based degradation — SoH declines with each equivalent full cycle
#   4. Efficiency degradation  — older batteries are slightly less efficient
# ─────────────────────────────────────────────────────────────────────────────


def compute_effective_capacity(soh: float) -> float:
    """Effective max MWh the battery can hold at the current SoH."""
    return BATTERY_MAX_MWH * soh


def compute_current_rt_efficiency(soh: float) -> float:
    """
    Round-trip efficiency degrades slightly with battery age.
    At SoH=1.0 → base RT eff (90%).  At SoH=0.70 → ~87.3%.
    Formula: RT_eff = base × (1 - factor × (1 - soh))
    """
    return BATTERY_ROUND_TRIP_EFF * (1.0 - BATTERY_EFF_DEGRADATION_FACTOR * (1.0 - soh))


def compute_charge_taper(soc: float) -> float:
    """
    SoC-dependent charge rate multiplier.
    Below TAPER_START (80%) → 1.0 (full rate).
    Above TAPER_START → linearly decreases to TAPER_MIN_MULT (0.20) at 100%.
    """
    if soc <= BATTERY_SOC_TAPER_START:
        return 1.0
    # Linear interpolation from 1.0 at taper_start to taper_min at 100%
    t = (soc - BATTERY_SOC_TAPER_START) / (1.0 - BATTERY_SOC_TAPER_START)
    return max(BATTERY_SOC_TAPER_MIN_MULT, 1.0 - t * (1.0 - BATTERY_SOC_TAPER_MIN_MULT))


def compute_soh(cycles: float) -> float:
    """
    Linear degradation model: SoH declines from 1.0 to EOL_SOH over CYCLE_LIFE.
    Beyond CYCLE_LIFE the battery still works at EOL_SOH (doesn't get worse).
    """
    if cycles >= BATTERY_CYCLE_LIFE:
        return BATTERY_EOL_SOH
    degradation = (1.0 - BATTERY_EOL_SOH) * (cycles / BATTERY_CYCLE_LIFE)
    return max(BATTERY_EOL_SOH, BATTERY_START_SOH - degradation)


def execute_battery_trade(
    action: str,
    mw: float,
    player: dict,
    de_price: float,
) -> dict:
    """
    Apply realistic battery physics to a trade and return the result.

    Returns a dict with all updated values and trade details, or an
    "error" key if the trade cannot proceed.

    PHYSICS APPLIED:
    ────────────────
    BUY (charge):
      1. Effective capacity = MAX_MWH × SoH
      2. Room = effective_cap - current_mwh
      3. SoC taper: if SoC > 80%, max charge rate is reduced
      4. Player pays for `mw` MWh at grid price
      5. Only mw × charge_eff actually enters the battery (energy loss)
      6. Half-cycle added to cumulative cycles → SoH updated

    SELL (discharge):
      1. Player's battery loses `mw` MWh
      2. Player only gets paid for mw × discharge_eff (energy loss)
      3. Half-cycle added → SoH updated
    """
    soh = player.get("battery_soh", BATTERY_START_SOH)
    cycles = player.get("battery_cycles", 0.0)
    battery_mwh = player["battery_mwh"]
    cash = player["cash"]

    effective_cap = compute_effective_capacity(soh)
    current_rt_eff = compute_current_rt_efficiency(soh)
    charge_eff = current_rt_eff**0.5
    discharge_eff = current_rt_eff**0.5

    if action == "BUY":
        # ── CHARGING ──
        room = effective_cap - battery_mwh
        if room <= 0:
            return {"error": "Battery is full! Cannot buy more."}

        # SoC-dependent taper: reduce max charge rate at high SoC
        soc = battery_mwh / effective_cap if effective_cap > 0 else 1.0
        taper_mult = compute_charge_taper(soc)
        max_charge_mw = BATTERY_MAX_MW * taper_mult
        mw = min(mw, max_charge_mw)

        # Can't charge more than available room (accounting for efficiency)
        # mw_stored = mw × charge_eff, and mw_stored <= room
        # So mw <= room / charge_eff
        max_mw_for_room = room / charge_eff if charge_eff > 0 else room
        mw = min(mw, max_mw_for_room)

        # Energy actually stored (after charge loss)
        mwh_stored = mw * charge_eff
        total_cost = mw * de_price  # Player pays for full grid withdrawal

        new_cash = cash - total_cost
        new_battery = battery_mwh + mwh_stored
        pnl_delta = -total_cost

        # Half-cycle: fraction of a full cycle (charge leg)
        half_cycle = mw / effective_cap if effective_cap > 0 else 0.0
        new_cycles = cycles + half_cycle * 0.5

    else:  # SELL
        # ── DISCHARGING ──
        if battery_mwh <= 0:
            return {"error": "Battery is empty! Cannot sell."}

        mw = min(mw, battery_mwh)  # Can't sell more than stored
        mw = min(mw, BATTERY_MAX_MW)  # Max discharge rate

        # Energy delivered to grid (after discharge loss)
        mwh_delivered = mw * discharge_eff
        total_cost = mwh_delivered * de_price  # Player gets paid for delivered energy

        new_cash = cash + total_cost
        new_battery = battery_mwh - mw  # Full amount leaves the battery
        pnl_delta = total_cost

        # Half-cycle (discharge leg)
        half_cycle = mw / effective_cap if effective_cap > 0 else 0.0
        new_cycles = cycles + half_cycle * 0.5

    # Update SoH based on new cumulative cycles
    new_soh = compute_soh(new_cycles)

    return {
        "action": action,
        "mw": mw,
        "de_price": de_price,
        "total_cost": total_cost,
        "new_cash": new_cash,
        "new_battery": new_battery,
        "pnl_delta": pnl_delta,
        "new_cycles": new_cycles,
        "new_soh": new_soh,
        "charge_eff": charge_eff,
        "discharge_eff": discharge_eff,
        "effective_cap": effective_cap,
        "rt_efficiency": current_rt_eff,
        "taper_mult": taper_mult if action == "BUY" else 1.0,
        "mwh_stored": mwh_stored if action == "BUY" else None,
        "mwh_delivered": mwh_delivered if action == "SELL" else None,
    }


@app.post("/api/trade")
async def execute_trade(request: Request):
    """
    Execute a trade (buy or sell electricity at the current German spot price).

    Requires header: Authorization: Bearer <token>
    Body: { "action": "BUY" | "SELL", "mw": 500 }

    REALISTIC BATTERY PHYSICS:
    ──────────────────────────
    BUY = charge the battery
        → Player pays for mw MWh at grid price
        → Only mw × charge_efficiency enters the battery (energy loss)
        → Charge rate tapers above 80% SoC
        → Half-cycle added to degradation counter

    SELL = discharge the battery
        → Battery loses mw MWh
        → Player gets paid for mw × discharge_efficiency (energy loss)
        → Half-cycle added to degradation counter

    DEGRADATION:
    ────────────
    Each trade adds a fractional cycle.  SoH declines linearly from 100%
    to 70% over 5,000 equivalent full cycles.  Efficiency also degrades
    slightly with age.
    """
    # Authenticate
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = auth[7:]

    player = await get_player_by_token(token)
    if not player:
        raise HTTPException(401, "Invalid token")

    # ── Rate limit check ──
    if not check_rate_limit(token):
        raise HTTPException(
            429,
            f"Rate limit exceeded. Max {TRADE_RATE_LIMIT} trades per {TRADE_RATE_WINDOW}s.",
        )

    body = await request.json()
    action = body.get("action", "").upper()
    mw = body.get("mw", 0)

    if action not in ("BUY", "SELL"):
        raise HTTPException(400, "Action must be BUY or SELL")
    if not isinstance(mw, (int, float)) or mw <= 0:
        raise HTTPException(400, "MW must be a positive number")

    # Enforce battery power limit (10 GW max) — taper may reduce further
    mw = min(mw, BATTERY_MAX_MW)

    # Get current German price
    de_price = game_state.prices.get("DE", 45.0)

    # ── Apply battery physics ──
    result = execute_battery_trade(action, mw, player, de_price)

    if "error" in result:
        raise HTTPException(400, result["error"])

    new_cash = result["new_cash"]
    new_battery = result["new_battery"]
    new_soh = result["new_soh"]
    new_cycles = result["new_cycles"]
    total_cost = result["total_cost"]
    traded_mw = result["mw"]
    pnl_delta = result["pnl_delta"]
    new_pnl = player["pnl"] + pnl_delta

    # ── Update SoH history ──
    soh_history = player.get("soh_history", [])
    soh_history.append(
        {
            "tick": game_state.tick,
            "day": game_state.day,
            "soh": round(new_soh, 4),
            "cycles": round(new_cycles, 2),
            "rt_eff": round(result["rt_efficiency"], 4),
        }
    )
    # Keep last 500 snapshots to limit storage
    if len(soh_history) > 500:
        soh_history = soh_history[-500:]

    # Update player in DB (including new physics columns)
    await db.execute(
        "UPDATE players SET cash = ?, battery_mwh = ?, cumulative_pnl = ?, battery_soh = ?, battery_cycles = ?, soh_history = ? WHERE id = ?",
        (
            new_cash,
            new_battery,
            new_pnl,
            new_soh,
            new_cycles,
            json.dumps(soh_history),
            player["id"],
        ),
    )

    # Record trade in audit log
    await db.execute(
        "INSERT INTO trades (player_id, tick, day, action, mw, price_eur_mwh, total_cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            player["id"],
            game_state.tick,
            game_state.day,
            action,
            traded_mw,
            de_price,
            total_cost,
            time.time(),
        ),
    )

    await db.commit()

    trade_pnl = compute_pnl_breakdown(new_cash, new_battery)
    return {
        "status": "executed",
        "action": action,
        "mw": round(traded_mw, 1),
        "price_eur_mwh": round(de_price, 2),
        "total_cost": round(total_cost, 2),
        "new_cash": round(new_cash, 2),
        "new_battery_mwh": round(new_battery, 1),
        "new_pnl": round(trade_pnl["total_pnl"], 2),
        "realized_pnl": round(trade_pnl["realized_pnl"], 2),
        "floating_pnl": round(trade_pnl["floating_pnl"], 2),
        # Battery physics feedback
        "battery_soh": round(new_soh, 4),
        "battery_cycles": round(new_cycles, 2),
        "effective_capacity_mwh": round(result["effective_cap"], 0),
        "rt_efficiency": round(result["rt_efficiency"], 4),
        "taper_mult": round(result["taper_mult"], 2),
        "mwh_stored": round(result["mwh_stored"], 1)
        if result.get("mwh_stored") is not None
        else None,
        "mwh_delivered": round(result["mwh_delivered"], 1)
        if result.get("mwh_delivered") is not None
        else None,
    }


@app.get("/health")
async def health_check():
    """Lightweight health endpoint for orchestrators (Docker, k8s, load balancers).

    Reports liveness of the HTTP app and readiness of the simulation engine.
    Does NOT touch the database so it stays fast and side-effect-free.
    """
    return JSONResponse(
        {
            "status": "ok",
            "version": app.version,
            "tick": game_state.tick,
            "day": game_state.day,
            "running": game_state.running,
            "paused": game_state.paused,
        }
    )


@app.get("/api/state")
async def get_state():
    """
    Get current full game state (REST fallback for clients without WebSocket).
    This returns the same payload that WebSocket clients receive every tick.
    """
    leaderboard = []
    if db:
        de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
        starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
        async with db.execute(
            """SELECT username, cash, battery_mwh,
                      cash AS realized_pnl,
                      (battery_mwh * ? - ?) AS floating_pnl
               FROM players
               ORDER BY realized_pnl DESC LIMIT 20""",
            (de_price, starting_value),
        ) as cursor:
            rows = await cursor.fetchall()
            for rank, row in enumerate(rows, 1):
                leaderboard.append(
                    {
                        "rank": rank,
                        "username": row[0],
                        "realized_pnl": round(row[3], 2),
                        "floating_pnl": round(row[4], 2),
                        "pnl": round(row[3] + row[4], 2),
                        "battery_mwh": round(row[2], 1),
                        "cash": round(row[1], 2),
                    }
                )

    return build_state_payload(leaderboard)


@app.get("/api/leaderboard")
async def get_leaderboard():
    """Get the top 50 traders sorted by realized PnL."""
    de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
    starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
    async with db.execute(
        """SELECT username, cash, battery_mwh,
                  cash AS realized_pnl,
                  (battery_mwh * ? - ?) AS floating_pnl
           FROM players
           ORDER BY realized_pnl DESC LIMIT 50""",
        (de_price, starting_value),
    ) as cursor:
        rows = await cursor.fetchall()

    return [
        {
            "rank": i + 1,
            "username": row[0],
            "realized_pnl": round(row[3], 2),
            "floating_pnl": round(row[4], 2),
            "pnl": round(row[3] + row[4], 2),
            "battery_mwh": round(row[2], 1),
            "cash": round(row[1], 2),
        }
        for i, row in enumerate(rows)
    ]


@app.get("/api/player")
async def get_player(request: Request):
    """Get current player's portfolio. Requires Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing Authorization header")
    token = auth[7:]

    player = await get_player_by_token(token)
    if not player:
        raise HTTPException(401, "Invalid token")

    pnl = compute_pnl_breakdown(player["cash"], player["battery_mwh"])
    return {
        "username": player["username"],
        "cash": round(player["cash"], 2),
        "battery_mwh": round(player["battery_mwh"], 1),
        "battery_pct": round(player["battery_mwh"] / BATTERY_MAX_MWH * 100, 1),
        "pnl": round(pnl["total_pnl"], 2),
        "realized_pnl": round(pnl["realized_pnl"], 2),
        "floating_pnl": round(pnl["floating_pnl"], 2),
    }


@app.get("/api/history")
async def get_price_history():
    """Get in-memory price history for all zones (last 72 ticks)."""
    return {
        zone_id: [round(p, 2) for p in prices]
        for zone_id, prices in game_state.price_history.items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11: WEBSOCKET ENDPOINT
# ─────────────────────────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket connection for real-time game state updates.

    Client connects with: ws://host/ws?token=<player_token>
    Server pushes full state JSON every tick (1 second).
    Client can send trade commands as JSON: {"action": "BUY", "mw": 1000}
    """
    token = ws.query_params.get("token", "")

    # Validate token
    player = await get_player_by_token(token)
    if not player:
        await ws.close(code=4001, reason="Invalid token")
        return

    await ws.accept()
    ws_clients[token] = ws

    # Send initial state immediately on connect
    leaderboard = []
    if db:
        de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
        starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
        async with db.execute(
            """SELECT username, cash, battery_mwh,
                      cash AS realized_pnl,
                      (battery_mwh * ? - ?) AS floating_pnl
               FROM players
               ORDER BY realized_pnl DESC LIMIT 20""",
            (de_price, starting_value),
        ) as cursor:
            rows = await cursor.fetchall()
            for rank, row in enumerate(rows, 1):
                leaderboard.append(
                    {
                        "rank": rank,
                        "username": row[0],
                        "realized_pnl": round(row[3], 2),
                        "floating_pnl": round(row[4], 2),
                        "pnl": round(row[3] + row[4], 2),
                        "battery_mwh": round(row[2], 1),
                        "cash": round(row[1], 2),
                    }
                )

    initial = build_state_payload(leaderboard)
    # Include player-specific data and portfolio analytics in initial push
    soh = player.get("battery_soh", BATTERY_START_SOH)
    effective_cap = compute_effective_capacity(soh)
    rt_eff = compute_current_rt_efficiency(soh)
    soc = player["battery_mwh"] / effective_cap if effective_cap > 0 else 1.0
    pnl = compute_pnl_breakdown(player["cash"], player["battery_mwh"])
    initial["player"] = {
        "username": player["username"],
        "cash": round(player["cash"], 2),
        "battery_mwh": round(player["battery_mwh"], 1),
        "pnl": round(pnl["total_pnl"], 2),
        "realized_pnl": round(pnl["realized_pnl"], 2),
        "floating_pnl": round(pnl["floating_pnl"], 2),
        "battery_soh": round(soh, 4),
        "battery_cycles": round(player.get("battery_cycles", 0.0), 2),
        "effective_capacity_mwh": round(effective_cap, 0),
        "rt_efficiency": round(rt_eff, 4),
        "soh_history": player.get("soh_history", []),
        "taper_mult": round(compute_charge_taper(soc), 2),
    }
    initial["analytics"] = await get_player_analytics(player["id"])
    await ws.send_text(json.dumps(initial))

    try:
        # Listen for trade commands from the client
        while True:
            data = await ws.receive_text()
            try:
                cmd = json.loads(data)

                if cmd.get("type") == "trade":
                    # Process trade via WebSocket (same physics as REST endpoint)
                    action = cmd.get("action", "").upper()
                    mw = cmd.get("mw", 0)

                    if (
                        action not in ("BUY", "SELL")
                        or not isinstance(mw, (int, float))
                        or mw <= 0
                    ):
                        await ws.send_text(
                            json.dumps({"error": "Invalid trade command"})
                        )
                        continue

                    # ── Rate limit check ──
                    if not check_rate_limit(token):
                        await ws.send_text(
                            json.dumps(
                                {
                                    "error": f"Rate limit exceeded. Max {TRADE_RATE_LIMIT} trades per {TRADE_RATE_WINDOW}s."
                                }
                            )
                        )
                        continue

                    mw = min(mw, BATTERY_MAX_MW)
                    de_price = game_state.prices.get("DE", 45.0)

                    # Re-fetch player state (may have changed)
                    player = await get_player_by_token(token)
                    if not player:
                        await ws.send_text(json.dumps({"error": "Player not found"}))
                        continue

                    # ── Apply battery physics (shared helper) ──
                    result = execute_battery_trade(action, mw, player, de_price)

                    if "error" in result:
                        await ws.send_text(json.dumps({"error": result["error"]}))
                        continue

                    new_cash = result["new_cash"]
                    new_battery = result["new_battery"]
                    new_soh = result["new_soh"]
                    new_cycles = result["new_cycles"]
                    total_cost = result["total_cost"]
                    traded_mw = result["mw"]
                    pnl_delta = result["pnl_delta"]
                    new_pnl = player["pnl"] + pnl_delta

                    # Update SoH history
                    soh_history = player.get("soh_history", [])
                    soh_history.append(
                        {
                            "tick": game_state.tick,
                            "day": game_state.day,
                            "soh": round(new_soh, 4),
                            "cycles": round(new_cycles, 2),
                            "rt_eff": round(result["rt_efficiency"], 4),
                        }
                    )
                    if len(soh_history) > 500:
                        soh_history = soh_history[-500:]

                    await db.execute(
                        "UPDATE players SET cash = ?, battery_mwh = ?, cumulative_pnl = ?, battery_soh = ?, battery_cycles = ?, soh_history = ? WHERE id = ?",
                        (
                            new_cash,
                            new_battery,
                            new_pnl,
                            new_soh,
                            new_cycles,
                            json.dumps(soh_history),
                            player["id"],
                        ),
                    )
                    await db.execute(
                        "INSERT INTO trades (player_id, tick, day, action, mw, price_eur_mwh, total_cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            player["id"],
                            game_state.tick,
                            game_state.day,
                            action,
                            traded_mw,
                            de_price,
                            total_cost,
                            time.time(),
                        ),
                    )
                    await db.commit()

                    # Fetch updated analytics after the trade
                    trade_analytics = await get_player_analytics(player["id"])

                    # Send confirmation back to this client
                    new_pnl = compute_pnl_breakdown(new_cash, new_battery)
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "trade_result",
                                "status": "executed",
                                "action": action,
                                "mw": round(traded_mw, 1),
                                "price_eur_mwh": round(de_price, 2),
                                "total_cost": round(total_cost, 2),
                                "player": {
                                    "cash": round(new_cash, 2),
                                    "battery_mwh": round(new_battery, 1),
                                    "pnl": round(new_pnl["total_pnl"], 2),
                                    "realized_pnl": round(new_pnl["realized_pnl"], 2),
                                    "floating_pnl": round(new_pnl["floating_pnl"], 2),
                                    "battery_soh": round(new_soh, 4),
                                    "battery_cycles": round(new_cycles, 2),
                                    "effective_capacity_mwh": round(
                                        result["effective_cap"], 0
                                    ),
                                    "rt_efficiency": round(result["rt_efficiency"], 4),
                                    "soh_history": soh_history,
                                },
                                "analytics": trade_analytics,
                                # Physics feedback for UI
                                "battery_physics": {
                                    "taper_mult": round(result["taper_mult"], 2),
                                    "mwh_stored": round(result["mwh_stored"], 1)
                                    if result.get("mwh_stored") is not None
                                    else None,
                                    "mwh_delivered": round(result["mwh_delivered"], 1)
                                    if result.get("mwh_delivered") is not None
                                    else None,
                                    "charge_eff": round(result["charge_eff"], 4),
                                    "discharge_eff": round(result["discharge_eff"], 4),
                                },
                            }
                        )
                    )

                elif cmd.get("type") == "get_player":
                    # Client requests fresh player data + portfolio analytics
                    player = await get_player_by_token(token)
                    if player:
                        analytics = await get_player_analytics(player["id"])
                        soh = player.get("battery_soh", BATTERY_START_SOH)
                        effective_cap = compute_effective_capacity(soh)
                        rt_eff = compute_current_rt_efficiency(soh)
                        soc = (
                            player["battery_mwh"] / effective_cap
                            if effective_cap > 0
                            else 1.0
                        )
                        p_pnl = compute_pnl_breakdown(
                            player["cash"], player["battery_mwh"]
                        )
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": "player_update",
                                    "player": {
                                        "username": player["username"],
                                        "cash": round(player["cash"], 2),
                                        "battery_mwh": round(player["battery_mwh"], 1),
                                        "pnl": round(p_pnl["total_pnl"], 2),
                                        "realized_pnl": round(p_pnl["realized_pnl"], 2),
                                        "floating_pnl": round(p_pnl["floating_pnl"], 2),
                                        "battery_soh": round(soh, 4),
                                        "battery_cycles": round(
                                            player.get("battery_cycles", 0.0), 2
                                        ),
                                        "effective_capacity_mwh": round(
                                            effective_cap, 0
                                        ),
                                        "rt_efficiency": round(rt_eff, 4),
                                        "soh_history": player.get("soh_history", []),
                                        "taper_mult": round(
                                            compute_charge_taper(soc), 2
                                        ),
                                    },
                                    "analytics": analytics,
                                }
                            )
                        )

                elif cmd.get("type") == "da_bid":
                    # ── Day-Ahead bid submission (new!) ──
                    # Client sends: {"type": "da_bid", "action": "BUY"|"SELL",
                    #                "mw": 1000, "bid_price": 42.0}
                    action = cmd.get("action", "").upper()
                    mw = cmd.get("mw", 0)
                    bid_price = cmd.get("bid_price", 0)

                    # ── Rate limit check ──
                    if not check_rate_limit(token):
                        await ws.send_text(
                            json.dumps(
                                {
                                    "error": f"Rate limit exceeded. Max {TRADE_RATE_LIMIT} trades per {TRADE_RATE_WINDOW}s."
                                }
                            )
                        )
                        continue

                    if action not in ("BUY", "SELL"):
                        await ws.send_text(
                            json.dumps({"error": "DA bid action must be BUY or SELL"})
                        )
                        continue
                    if not isinstance(mw, (int, float)) or mw <= 0:
                        await ws.send_text(
                            json.dumps({"error": "DA bid MW must be positive"})
                        )
                        continue
                    if not isinstance(bid_price, (int, float)):
                        await ws.send_text(
                            json.dumps({"error": "DA bid price must be a number"})
                        )
                        continue
                    if game_state.da_cleared_today:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "error": "DA auction already cleared today. Bid for tomorrow."
                                }
                            )
                        )
                        continue

                    mw = min(mw, BATTERY_MAX_MW)

                    # Re-fetch player to get their name
                    player = await get_player_by_token(token)
                    if not player:
                        await ws.send_text(json.dumps({"error": "Player not found"}))
                        continue

                    # Remove any existing bid from this player (one bid per player)
                    game_state.da_bids = [
                        b for b in game_state.da_bids if b.player_id != player["id"]
                    ]

                    bid = DABid(
                        player_id=player["id"],
                        player_name=player["username"],
                        action=action,
                        mw=mw,
                        bid_price=bid_price,
                        submitted_tick=game_state.tick,
                        submitted_day=game_state.day,
                    )
                    game_state.da_bids.append(bid)

                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "da_bid_confirmed",
                                "action": action,
                                "mw": round(mw, 1),
                                "bid_price": round(bid_price, 2),
                                "message": f"DA bid submitted: {action} {round(mw)}MW @ \u20ac{bid_price:.2f}. Clears at {DA_CLEARING_HOUR}:00.",
                            }
                        )
                    )

            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"error": "Invalid JSON"}))

    except WebSocketDisconnect:
        ws_clients.pop(token, None)
        _trade_timestamps.pop(token, None)
    except Exception:
        ws_clients.pop(token, None)
        _trade_timestamps.pop(token, None)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12: ADMIN ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/admin/reset")
async def admin_reset(request: Request):
    """
    ADMIN ONLY: Reset the entire game state and wipe the database.

    Requires query parameter: ?key=optimus_admin_2026
    This is a destructive operation — all player data and history is lost.

    Usage: POST http://localhost:8000/admin/reset?key=optimus_admin_2026
    """
    global game_state

    key = request.query_params.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    # Wipe database tables
    await db.execute("DELETE FROM trades")
    await db.execute("DELETE FROM price_history")
    await db.execute("DELETE FROM players")
    await db.execute("DELETE FROM game_sessions")
    await db.commit()

    # Reset in-memory state
    game_state = initialize_game_state()

    # Disconnect all WebSocket clients
    for token, ws in list(ws_clients.items()):
        try:
            await ws.close(code=4000, reason="Game reset by admin")
        except Exception:
            pass
    ws_clients.clear()

    return {"status": "Game state and database reset successfully"}


@app.post("/admin/speed")
async def admin_set_speed(request: Request):
    """
    ADMIN ONLY: Change the game simulation speed.

    Requires JSON body: {"speed": <float>, "key": "<admin_key>"}
    Speed is clamped to [0.5, 10.0]:
      - 0.5 = fast (0.5s per tick)
      - 1.0 = normal (1s per tick)
      - 2.0 = slow (2s per tick, gives students more time)
      - 5.0 = very slow (5s per tick)

    Usage: POST http://localhost:8000/admin/speed
           Body: {"speed": 2.0, "key": "optimus_admin_2026"}
    """
    body = await request.json()
    key = body.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    speed = body.get("speed", 1.0)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        raise HTTPException(400, "speed must be a number")

    # Clamp to reasonable range
    speed = max(0.5, min(10.0, speed))
    game_state.game_speed = speed

    print(f"[ADMIN] Game speed changed to {speed}x ({speed}s per tick)")
    return {"status": f"Game speed set to {speed}x", "game_speed": speed}


@app.post("/admin/pause")
async def admin_pause(request: Request):
    """
    ADMIN ONLY: Toggle game pause state.

    Requires JSON body: {"key": "<admin_key>"}
    Returns the new paused state.
    """
    body = await request.json()
    key = body.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    game_state.paused = not game_state.paused
    state_str = "PAUSED" if game_state.paused else "RESUMED"
    print(f"[ADMIN] Game {state_str}")
    return {"status": f"Game {state_str}", "paused": game_state.paused}


@app.post("/admin/trigger_event")
async def admin_trigger_event(request: Request):
    """
    ADMIN ONLY: Manually trigger a game event.

    Requires JSON body: {"key": "<admin_key>", "event_type": "plant_outage", "zone": "DE"}
    For interconnector_fault, use "ic" instead of "zone": {"ic": "DE-FR"}
    """
    body = await request.json()
    key = body.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    event_type = body.get("event_type", "")
    zone = body.get("zone", "")
    ic = body.get("ic", "")

    # Find the matching template
    template = None
    for t in EVENT_TEMPLATES:
        if t["type"] == event_type:
            template = t
            break

    if not template:
        valid_types = [t["type"] for t in EVENT_TEMPLATES]
        raise HTTPException(400, f"Unknown event_type. Valid: {valid_types}")

    # Build the event
    duration = random.randint(
        template["duration_range"][0], template["duration_range"][1]
    )
    headline = random.choice(template["headlines"])

    if event_type == "interconnector_fault":
        if ic not in INTERCONNECTORS:
            raise HTTPException(
                400, f"Unknown IC. Valid: {list(INTERCONNECTORS.keys())}"
            )
        event = GameEvent(
            event_type=event_type,
            headline=headline.replace("{ic}", ic),
            affected_zones=[],
            affected_ic=ic,
            price_mod=template["price_mod"],
            ticks_remaining=duration,
            started_tick=game_state.tick,
            started_day=game_state.day,
        )
    elif event_type == "carbon_price_spike":
        # Carbon affects all zones
        event = GameEvent(
            event_type=event_type,
            headline=headline,
            affected_zones=list(ZONES.keys()),
            affected_ic=None,
            price_mod=template["price_mod"],
            ticks_remaining=duration,
            started_tick=game_state.tick,
            started_day=game_state.day,
        )
    else:
        if zone not in ZONES:
            raise HTTPException(400, f"Unknown zone. Valid: {list(ZONES.keys())}")
        event = GameEvent(
            event_type=event_type,
            headline=headline.replace("{zone}", zone),
            affected_zones=[zone],
            affected_ic=None,
            price_mod=template["price_mod"],
            ticks_remaining=duration,
            started_tick=game_state.tick,
            started_day=game_state.day,
        )

    game_state.active_events.append(event)
    game_state.event_log.append(
        {
            "type": event.event_type,
            "headline": event.headline,
            "zones": event.affected_zones,
            "ic": event.affected_ic,
            "ticks_remaining": event.ticks_remaining,
            "status": "BREAKING",
            "day": game_state.day,
            "tick": game_state.tick,
        }
    )

    print(f"[ADMIN] Triggered event: {event_type} — {event.headline}")
    return {
        "status": "Event triggered",
        "event_type": event_type,
        "headline": event.headline,
        "duration": duration,
    }


@app.get("/admin/players")
async def admin_get_players(request: Request):
    """
    ADMIN ONLY: Get all player data for monitoring.

    Requires query parameter: ?key=<admin_key>
    """
    key = request.query_params.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    if not db:
        return []

    de_price = game_state.prices.get("DE", STARTING_REF_PRICE)
    starting_value = BATTERY_START_MWH * STARTING_REF_PRICE
    players = []
    async with db.execute(
        """SELECT id, username, cash, battery_mwh, battery_soh, battery_cycles,
                  cash AS realized_pnl,
                  (battery_mwh * ? - ?) AS floating_pnl,
                  created_at
           FROM players ORDER BY realized_pnl DESC""",
        (de_price, starting_value),
    ) as cursor:
        rows = await cursor.fetchall()
        for row in rows:
            players.append(
                {
                    "id": row[0],
                    "username": row[1],
                    "cash": round(row[2], 2),
                    "battery_mwh": round(row[3], 1),
                    "battery_soh": round(row[4], 4) if row[4] else 1.0,
                    "battery_cycles": round(row[5], 2) if row[5] else 0.0,
                    "realized_pnl": round(row[6], 2),
                    "floating_pnl": round(row[7], 2),
                    "total_pnl": round(row[6] + row[7], 2),
                    "created_at": row[8],
                }
            )
    return players


@app.get("/admin/stats")
async def admin_get_stats(request: Request):
    """
    ADMIN ONLY: Get game statistics summary.

    Requires query parameter: ?key=<admin_key>
    """
    key = request.query_params.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    trade_count = 0
    player_count = 0
    if db:
        async with db.execute("SELECT COUNT(*) FROM trades") as cursor:
            trade_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM players") as cursor:
            player_count = (await cursor.fetchone())[0]

    return {
        "tick": game_state.tick,
        "day": game_state.day,
        "paused": game_state.paused,
        "game_speed": game_state.game_speed,
        "connected_clients": len(ws_clients),
        "total_players": player_count,
        "total_trades": trade_count,
        "active_events": len(game_state.active_events),
        "prices": {z: round(p, 2) for z, p in game_state.prices.items()},
    }


@app.post("/admin/save")
async def admin_save_game(request: Request):
    """
    ADMIN ONLY: Save the current game state to a named slot.

    Requires JSON body: {"key": "<admin_key>", "name": "my_save"}
    """
    body = await request.json()
    key = body.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Save name is required")

    state_json = serialize_game_state(game_state)
    await db.execute(
        "INSERT OR REPLACE INTO game_sessions (name, state_json, tick, day, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, state_json, game_state.tick, game_state.day, time.time()),
    )
    await db.commit()

    print(
        f"[ADMIN] Game saved as '{name}' (Day {game_state.day}, Hour {game_state.tick})"
    )
    return {
        "status": f"Game saved as '{name}' (Day {game_state.day}, Hour {game_state.tick})"
    }


@app.post("/admin/load")
async def admin_load_game(request: Request):
    """
    ADMIN ONLY: Load a saved game state by name.

    Requires JSON body: {"key": "<admin_key>", "name": "my_save"}
    """
    global game_state
    body = await request.json()
    key = body.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Save name is required")

    async with db.execute(
        "SELECT state_json FROM game_sessions WHERE name = ?", (name,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"No save found with name '{name}'")

    restored = deserialize_game_state(row[0])
    restored.running = True
    restored.paused = game_state.paused  # Preserve current pause state
    game_state = restored

    print(
        f"[ADMIN] Game loaded from '{name}' (Day {game_state.day}, Hour {game_state.tick})"
    )
    return {
        "status": f"Game loaded from '{name}' (Day {game_state.day}, Hour {game_state.tick})"
    }


@app.get("/admin/saves")
async def admin_list_saves(request: Request):
    """
    ADMIN ONLY: List all saved game sessions.

    Requires query parameter: ?key=<admin_key>
    """
    key = request.query_params.get("key", "")
    if key != ADMIN_KEY:
        raise HTTPException(403, "Invalid admin key")

    saves = []
    if db:
        async with db.execute(
            "SELECT name, tick, day, created_at FROM game_sessions ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                saves.append(
                    {
                        "name": row[0],
                        "tick": row[1],
                        "day": row[2],
                        "created_at": row[3],
                    }
                )
    return saves


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """Full admin dashboard with game controls, event triggers, player monitoring, and save/load."""
    return HTMLResponse(
        content="""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Battery Trader Sim — Admin</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * { box-sizing: border-box; }
            body { background: #0a0a0a; color: #00ff88; font-family: 'Courier New', monospace; padding: 20px; margin: 0; }
            h1 { color: #ff4444; margin-bottom: 5px; }
            h2 { margin-top: 0; margin-bottom: 10px; font-size: 16px; }
            button { background: #0066cc; color: white; padding: 10px 18px; border: none;
                     cursor: pointer; font-size: 14px; font-family: monospace; margin: 4px 2px; border-radius: 4px; }
            button:hover { background: #0088ff; }
            .btn-danger { background: #ff4444; }
            .btn-danger:hover { background: #ff6666; }
            .btn-success { background: #00aa44; }
            .btn-success:hover { background: #00cc55; }
            .btn-warning { background: #cc8800; }
            .btn-warning:hover { background: #ee9900; }
            .speed-btn.active { background: #00ff88; color: #0a0a0a; font-weight: bold; }
            .info { color: #888; margin: 5px 0; font-size: 13px; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 15px; }
            .panel { background: #111; border: 1px solid #333; border-radius: 6px; padding: 15px; }
            .speed-controls { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
            select, input[type=text], input[type=number] {
                background: #1a1a1a; color: #00ff88; border: 1px solid #333; padding: 8px;
                font-family: monospace; font-size: 14px; border-radius: 4px; }
            select:focus, input:focus { border-color: #00ff88; outline: none; }
            table { width: 100%; border-collapse: collapse; font-size: 13px; }
            th { text-align: left; color: #888; border-bottom: 1px solid #333; padding: 6px 8px; }
            td { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
            .positive { color: #00ff88; }
            .negative { color: #ff4444; }
            .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 10px 0; }
            .stat-box { background: #1a1a1a; border: 1px solid #333; border-radius: 4px; padding: 10px; text-align: center; }
            .stat-value { font-size: 24px; font-weight: bold; }
            .stat-label { font-size: 11px; color: #888; margin-top: 4px; }
            .status-msg { margin-top: 8px; padding: 8px; font-size: 13px; min-height: 20px; }
            .pause-indicator { display: inline-block; padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 13px; }
            .pause-indicator.paused { background: #ff4444; color: white; }
            .pause-indicator.running { background: #00aa44; color: white; }
            .event-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
            #savesList { max-height: 200px; overflow-y: auto; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } .stat-grid { grid-template-columns: repeat(2, 1fr); } }
        </style>
    </head>
    <body>
        <h1>ADMIN PANEL</h1>
        <p class="info">Battery Trader Sim — Game Administration | <span id="clockDisplay">Day 1, Hour 0</span></p>

        <!-- ── LIVE STATS ── -->
        <div class="stat-grid" id="statsGrid">
            <div class="stat-box"><div class="stat-value" id="statDay">-</div><div class="stat-label">DAY / HOUR</div></div>
            <div class="stat-box"><div class="stat-value" id="statPlayers">-</div><div class="stat-label">PLAYERS</div></div>
            <div class="stat-box"><div class="stat-value" id="statConnected">-</div><div class="stat-label">CONNECTED</div></div>
            <div class="stat-box"><div class="stat-value" id="statTrades">-</div><div class="stat-label">TOTAL TRADES</div></div>
        </div>

        <div class="grid">
            <!-- ── LEFT COLUMN ── -->
            <div>
                <!-- PAUSE / RESUME -->
                <div class="panel">
                    <h2 style="color: #ff8800;">Game Control</h2>
                    <div style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap;">
                        <button id="pauseBtn" class="btn-warning" onclick="togglePause()">PAUSE</button>
                        <span id="pauseIndicator" class="pause-indicator running">RUNNING</span>
                    </div>
                    <div class="status-msg" id="pauseStatus"></div>
                </div>

                <!-- SPEED CONTROLS -->
                <div class="panel" style="margin-top: 10px;">
                    <h2 style="color: #0088ff;">Game Speed</h2>
                    <p class="info">Higher = slower (more time for students). 1x = 1s per tick.</p>
                    <div class="speed-controls">
                        <button class="speed-btn" onclick="setSpeed(0.5)">0.5x</button>
                        <button class="speed-btn active" onclick="setSpeed(1)">1x</button>
                        <button class="speed-btn" onclick="setSpeed(2)">2x</button>
                        <button class="speed-btn" onclick="setSpeed(3)">3x</button>
                        <button class="speed-btn" onclick="setSpeed(5)">5x</button>
                        <button class="speed-btn" onclick="setSpeed(10)">10x</button>
                    </div>
                    <div class="status-msg" id="speedStatus"></div>
                </div>

                <!-- EVENT TRIGGER -->
                <div class="panel" style="margin-top: 10px;">
                    <h2 style="color: #cc44ff;">Trigger Event</h2>
                    <p class="info">Manually inject a market event into the simulation.</p>
                    <div class="event-row">
                        <select id="eventType" onchange="updateEventTargets()">
                            <option value="plant_outage">Plant Outage (+15 EUR)</option>
                            <option value="demand_shock_heat">Heat Wave (+12 EUR)</option>
                            <option value="demand_shock_cold">Cold Snap (+10 EUR)</option>
                            <option value="carbon_price_spike">Carbon Spike (+8 EUR, all zones)</option>
                            <option value="interconnector_fault">IC Fault (halves capacity)</option>
                            <option value="renewable_subsidy">Renewable Subsidy (-8 EUR)</option>
                            <option value="lng_cargo_arrival">LNG Cargo (-6 EUR)</option>
                            <option value="grid_emergency">Grid Emergency (+25 EUR)</option>
                        </select>
                        <select id="eventTarget">
                            <option value="DE">DE — Germany</option>
                            <option value="FR">FR — France</option>
                            <option value="NL">NL — Netherlands</option>
                            <option value="DK">DK — Denmark</option>
                            <option value="PL">PL — Poland</option>
                        </select>
                        <button class="btn-warning" onclick="triggerEvent()">TRIGGER</button>
                    </div>
                    <div class="status-msg" id="eventStatus"></div>
                </div>

                <!-- SAVE / LOAD -->
                <div class="panel" style="margin-top: 10px;">
                    <h2 style="color: #00cccc;">Save / Load Game</h2>
                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                        <input type="text" id="saveName" placeholder="Save name..." style="flex: 1; min-width: 150px;">
                        <button class="btn-success" onclick="saveGame()">SAVE</button>
                        <button onclick="loadSaves()">REFRESH LIST</button>
                    </div>
                    <div id="savesList" style="margin-top: 10px;"></div>
                    <div class="status-msg" id="saveStatus"></div>
                </div>

                <!-- DANGER ZONE -->
                <div class="panel" style="margin-top: 10px;">
                    <h2 style="color: #ff4444;">Danger Zone</h2>
                    <p class="info">This will wipe ALL player data, trade history, and reset the simulation.</p>
                    <button class="btn-danger" onclick="resetGame()">RESET ENTIRE GAME</button>
                    <div class="status-msg" id="resetStatus"></div>
                </div>
            </div>

            <!-- ── RIGHT COLUMN: PLAYER MONITORING ── -->
            <div>
                <div class="panel" style="height: 100%; display: flex; flex-direction: column;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <h2 style="color: #ffcc00;">Player Monitoring</h2>
                        <button onclick="loadPlayers()" style="padding: 6px 12px; font-size: 12px;">REFRESH</button>
                    </div>
                    <div style="overflow-y: auto; flex: 1;">
                        <table>
                            <thead>
                                <tr><th>#</th><th>Player</th><th>Cash</th><th>Battery</th><th>SoH</th><th>Realized</th><th>Floating</th><th>Total PnL</th></tr>
                            </thead>
                            <tbody id="playerTable">
                                <tr><td colspan="8" style="color: #888;">Click REFRESH to load...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <script>
        let adminKey = null;
        let refreshInterval = null;

        function getKey() {
            if (!adminKey) { adminKey = prompt('Enter admin key:'); }
            return adminKey;
        }

        // ── Auto-refresh stats ──
        async function loadStats() {
            const key = getKey();
            if (!key) return;
            try {
                const res = await fetch('/admin/stats?key=' + encodeURIComponent(key));
                if (!res.ok) { if (res.status === 403) adminKey = null; return; }
                const d = await res.json();
                document.getElementById('statDay').textContent = 'D' + d.day + ' H' + d.tick;
                document.getElementById('statPlayers').textContent = d.total_players;
                document.getElementById('statConnected').textContent = d.connected_clients;
                document.getElementById('statTrades').textContent = d.total_trades;
                document.getElementById('clockDisplay').textContent = 'Day ' + d.day + ', Hour ' + d.tick;
                // Update pause indicator
                const pi = document.getElementById('pauseIndicator');
                const pb = document.getElementById('pauseBtn');
                if (d.paused) {
                    pi.textContent = 'PAUSED'; pi.className = 'pause-indicator paused';
                    pb.textContent = 'RESUME'; pb.className = 'btn-success';
                } else {
                    pi.textContent = 'RUNNING'; pi.className = 'pause-indicator running';
                    pb.textContent = 'PAUSE'; pb.className = 'btn-warning';
                }
            } catch (e) {}
        }

        // ── Pause/Resume ──
        async function togglePause() {
            const key = getKey(); if (!key) return;
            try {
                const res = await fetch('/admin/pause', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key }) });
                const d = await res.json();
                if (res.ok) {
                    document.getElementById('pauseStatus').textContent = d.status;
                    loadStats();
                } else { adminKey = null; document.getElementById('pauseStatus').textContent = 'Error: ' + (d.detail || ''); }
            } catch (e) { document.getElementById('pauseStatus').textContent = 'Error: ' + e.message; }
        }

        // ── Speed ──
        async function setSpeed(speed) {
            const key = getKey(); if (!key) return;
            try {
                const res = await fetch('/admin/speed', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ speed, key }) });
                const d = await res.json();
                if (res.ok) {
                    document.getElementById('speedStatus').textContent = d.status;
                    document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
                    event.target.classList.add('active');
                } else { adminKey = null; document.getElementById('speedStatus').textContent = 'Error: ' + (d.detail || ''); }
            } catch (e) { document.getElementById('speedStatus').textContent = 'Error: ' + e.message; }
        }

        // ── Event target selector ──
        function updateEventTargets() {
            const type = document.getElementById('eventType').value;
            const sel = document.getElementById('eventTarget');
            sel.innerHTML = '';
            if (type === 'interconnector_fault') {
                ['DE-FR','DE-NL','DE-DK','DE-PL'].forEach(ic => {
                    sel.innerHTML += '<option value="' + ic + '">' + ic + '</option>';
                });
            } else if (type === 'carbon_price_spike') {
                sel.innerHTML = '<option value="ALL">ALL ZONES</option>';
                sel.disabled = true;
            } else {
                sel.disabled = false;
                ['DE','FR','NL','DK','PL'].forEach(z => {
                    sel.innerHTML += '<option value="' + z + '">' + z + '</option>';
                });
            }
        }

        // ── Trigger Event ──
        async function triggerEvent() {
            const key = getKey(); if (!key) return;
            const eventType = document.getElementById('eventType').value;
            const target = document.getElementById('eventTarget').value;
            const body = { key, event_type: eventType };
            if (eventType === 'interconnector_fault') body.ic = target;
            else if (eventType !== 'carbon_price_spike') body.zone = target;
            try {
                const res = await fetch('/admin/trigger_event', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body) });
                const d = await res.json();
                if (res.ok) { document.getElementById('eventStatus').textContent = d.headline + ' (duration: ' + d.duration + 'h)'; }
                else { document.getElementById('eventStatus').textContent = 'Error: ' + (d.detail || ''); }
            } catch (e) { document.getElementById('eventStatus').textContent = 'Error: ' + e.message; }
        }

        // ── Players ──
        async function loadPlayers() {
            const key = getKey(); if (!key) return;
            try {
                const res = await fetch('/admin/players?key=' + encodeURIComponent(key));
                if (!res.ok) { if (res.status === 403) adminKey = null; return; }
                const players = await res.json();
                const tbody = document.getElementById('playerTable');
                if (players.length === 0) { tbody.innerHTML = '<tr><td colspan="8" style="color:#888;">No players yet</td></tr>'; return; }
                tbody.innerHTML = players.map((p, i) =>
                    '<tr>' +
                    '<td>' + (i+1) + '</td>' +
                    '<td>' + p.username + '</td>' +
                    '<td>' + p.cash.toLocaleString('en', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</td>' +
                    '<td>' + Math.round(p.battery_mwh).toLocaleString() + '</td>' +
                    '<td>' + (p.battery_soh * 100).toFixed(1) + '%</td>' +
                    '<td class="' + (p.realized_pnl >= 0 ? 'positive' : 'negative') + '">' + p.realized_pnl.toLocaleString('en', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</td>' +
                    '<td class="' + (p.floating_pnl >= 0 ? 'positive' : 'negative') + '">' + p.floating_pnl.toLocaleString('en', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</td>' +
                    '<td class="' + (p.total_pnl >= 0 ? 'positive' : 'negative') + '" style="font-weight:bold;">' + p.total_pnl.toLocaleString('en', {minimumFractionDigits: 0, maximumFractionDigits: 0}) + '</td>' +
                    '</tr>'
                ).join('');
            } catch (e) {}
        }

        // ── Save / Load ──
        async function saveGame() {
            const key = getKey(); if (!key) return;
            const name = document.getElementById('saveName').value.trim() || 'quicksave';
            try {
                const res = await fetch('/admin/save', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key, name }) });
                const d = await res.json();
                document.getElementById('saveStatus').textContent = res.ok ? d.status : ('Error: ' + (d.detail || ''));
                if (res.ok) loadSaves();
            } catch (e) { document.getElementById('saveStatus').textContent = 'Error: ' + e.message; }
        }

        async function loadSaves() {
            const key = getKey(); if (!key) return;
            try {
                const res = await fetch('/admin/saves?key=' + encodeURIComponent(key));
                if (!res.ok) return;
                const saves = await res.json();
                const div = document.getElementById('savesList');
                if (saves.length === 0) { div.innerHTML = '<p class="info">No saves yet.</p>'; return; }
                div.innerHTML = '<table><thead><tr><th>Name</th><th>Day/Hour</th><th>Saved</th><th></th></tr></thead><tbody>' +
                    saves.map(s =>
                        '<tr><td>' + s.name + '</td><td>D' + (s.day || '?') + ' H' + (s.tick || '?') + '</td>' +
                        '<td>' + new Date(s.created_at * 1000).toLocaleString() + '</td>' +
                        '<td><button onclick="loadGame(\'' + s.name.replace(/'/g, "\\\\'") + '\')" style="padding:4px 10px;font-size:12px;">LOAD</button></td></tr>'
                    ).join('') + '</tbody></table>';
            } catch (e) {}
        }

        async function loadGame(name) {
            if (!confirm('Load save "' + name + '"? Current game state will be replaced.')) return;
            const key = getKey(); if (!key) return;
            try {
                const res = await fetch('/admin/load', { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key, name }) });
                const d = await res.json();
                document.getElementById('saveStatus').textContent = res.ok ? d.status : ('Error: ' + (d.detail || ''));
                if (res.ok) { loadStats(); loadPlayers(); }
            } catch (e) { document.getElementById('saveStatus').textContent = 'Error: ' + e.message; }
        }

        // ── Reset ──
        async function resetGame() {
            if (!confirm('Are you sure? This will DELETE all player data and reset the game.')) return;
            if (!confirm('REALLY sure? This cannot be undone.')) return;
            const key = getKey(); if (!key) return;
            const res = await fetch('/admin/reset?key=' + encodeURIComponent(key), { method: 'POST' });
            const d = await res.json();
            document.getElementById('resetStatus').textContent = JSON.stringify(d);
            loadStats(); loadPlayers();
        }

        // ── Init ──
        loadStats();
        refreshInterval = setInterval(loadStats, 3000);
        </script>
    </body>
    </html>
    """
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 13: ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
