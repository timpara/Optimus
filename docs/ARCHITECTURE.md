# Architecture

This document explains how the gridplay server is put together so contributors
can navigate the code with confidence.

## High-level components

```
┌────────────────────────────────────────────────────────────────┐
│                         FastAPI app                            │
│                                                                │
│  /            ──►  index.html (SPA)                            │
│  /api/*       ──►  REST: auth, trade, admin, health            │
│  /ws          ──►  WebSocket: game-state broadcast + trades    │
│                                                                │
│                    ▲                     ▲                     │
│                    │                     │                     │
└────────────────────┼─────────────────────┼─────────────────────┘
                     │                     │
             ┌───────┴────────┐   ┌────────┴────────┐
             │  GameState     │   │   SQLite        │
             │  (in-memory)   │   │   (per-player)  │
             └───────▲────────┘   └─────────────────┘
                     │
             ┌───────┴────────────────┐
             │  Tick loop (asyncio)   │
             │  1 real second = 1 h   │
             └────────────────────────┘
```

Everything runs in a single process. The background task ticks once per
`GRIDPLAY_TICK_INTERVAL` seconds; after each tick the full `GameState` is
broadcast to every connected WebSocket client.

## Tick loop

Each tick executes, in order:

1. **Save prior forecasts** — copy `forecast[0]` into `WeatherState.forecasted_*`
   so the new actuals can be compared against them.
2. **Advance weather** — stochastic wind (Ornstein–Uhlenbeck) + deterministic
   solar + temperature.
3. **Spawn/expire events** — plant outages, heat waves, carbon spikes, etc.
4. **Calculate raw (pre-coupling) prices** — per zone, split into an expected
   component and a surprise component (amplified by `SURPRISE_AMPLIFIER`).
5. **Apply market coupling** — iterative relaxation across interconnectors
   with hard capacity limits; flows above 95 % of capacity flag congestion.
6. **Clear day-ahead auction** if `hour == DA_CLEARING_HOUR`.
7. **Generate next-hour forecasts** — deterministic projection + growing
   Gaussian noise.
8. **Record price history** and advance the clock.
9. **Broadcast** the new state to all WebSocket clients.

## Pricing model

Raw zonal price:

```
price = base_price
      - expected_wind_effect        # already priced in by DA market
      - surprise_wind_effect × 1.5  # real-time deviation, amplified
      - expected_solar_effect
      - surprise_solar_effect × 1.5
      + demand_effect               # duck curve
      + event_modifiers
      + noise
```

Coupling then redistributes supply across interconnectors until flows hit
capacity limits — creating the familiar *congestion rent* that students
observe on the map.

## Battery physics

Each player owns a simulated battery with:

- Round-trip efficiency ~90 % (split symmetrically as √0.9 per leg).
- SoC-dependent charge taper above 80 %.
- Tiny self-discharge every tick.
- Cycle-based SoH degradation from 100 % → 70 % over 5 000 equivalent full
  cycles.
- Efficiency degradation tied to SoH.

See `main.py` (`SECTION 1`) for the exact constants and the battery update
routines for the math.

## Persistence

Only player state is durable:

- `players` — username, hashed password, cash, battery MWh, SoH, cycles.
- `trades` — full trade log for analytics and average buy/sell prices.
- `price_history` — rolling window of the last N ticks per zone.

The market state itself is rebuilt from scratch at each restart; this is
intentional so resetting a classroom session is a single command.

## Frontend

A single-page application served from `index.html`:

- **Leaflet** for the map + congestion-colored interconnectors.
- **Chart.js** for price/health time series.
- **Tailwind (CDN)** for the Bloomberg-terminal aesthetic.
- Plain WebSocket + `fetch` — no framework, no build step.

## Where to look

| I want to change…                      | Look here                        |
|---------------------------------------|----------------------------------|
| Market zones, base prices, sensitivities | `ZONES` table in `main.py`    |
| Interconnector capacities             | `INTERCONNECTORS` table          |
| Event probabilities / headlines       | `EVENT_TEMPLATES`                |
| Battery physics constants             | `BATTERY_*` constants            |
| DA clearing logic                     | `clear_day_ahead` function       |
| Pricing formula                       | `calculate_base_prices`          |
| Coupling algorithm                    | `apply_market_coupling`          |
| API endpoints                         | `@app.post/@app.get` routes      |
| Frontend layout / styles              | `index.html`                     |
