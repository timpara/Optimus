# Optimus — Battery Trader Sim

[![CI](https://github.com/timpara/Optimus/actions/workflows/ci.yml/badge.svg)](https://github.com/timpara/Optimus/actions/workflows/ci.yml)
[![Docker](https://github.com/timpara/Optimus/actions/workflows/docker-release.yml/badge.svg)](https://github.com/timpara/Optimus/actions/workflows/docker-release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Release](https://img.shields.io/github/v/release/timpara/Optimus)](https://github.com/timpara/Optimus/releases)

> A multiplayer, browser-based educational energy trading game. Students manage
> a 50 GWh / 10 GW grid battery in Germany and trade on a simulated multi-zone
> European spot market — learning about market coupling, interconnector
> congestion, renewable intermittency, the duck curve, and forecast risk.

![Battery Trader Sim — dark-theme trading UI](docs/screenshot.png)

> _Screenshot above is a placeholder path — drop a real PNG at `docs/screenshot.png` after running the app locally._

---

## Features

- **Real-time multi-zone market simulation** — Germany, France, Netherlands,
  Denmark, Poland. Each zone has its own generation mix (nuclear, wind, solar,
  gas, coal) and demand profile.
- **Market coupling** with HVDC interconnectors and hard capacity limits:
  students watch prices converge when lines have headroom and diverge when
  they congest — just like real European markets.
- **Stochastic weather** driven by Ornstein–Uhlenbeck wind + sinusoidal solar,
  with a forecast layer and a "surprise amplifier" that teaches the difference
  between expected and realized conditions.
- **Day-ahead auction** with uniform-price clearing, alongside a real-time
  intraday trade mechanism.
- **Battery physics**: round-trip efficiency, SoC-dependent charge taper,
  self-discharge, cycle-based State of Health (SoH) degradation.
- **Event engine**: plant outages, heat waves, carbon spikes, cable faults,
  LNG cargos — each with a news-ticker headline.
- **Live leaderboard**, portfolio analytics, mark-to-market PnL, per-player
  persistence (SQLite).
- **Zero build step** frontend — Tailwind + Leaflet + Chart.js via CDN.
- Single `docker compose up` to run a class of students.

---

## Quickstart

### Docker (recommended)

```bash
# Public image on GHCR
docker run --rm -p 8000:8000 \
  -e OPTIMUS_CLASS_PASSWORD="trade2026" \
  -e OPTIMUS_ADMIN_KEY="change-me" \
  -v optimus-data:/data \
  ghcr.io/timpara/optimus:latest
```

Or with `docker compose` (persists the SQLite DB across restarts):

```bash
git clone https://github.com/timpara/Optimus.git
cd Optimus
cp .env.example .env     # edit secrets
docker compose up -d
```

Open <http://localhost:8000>.

### Local development

Requires Python 3.12+.

```bash
git clone https://github.com/timpara/Optimus.git
cd Optimus
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

export OPTIMUS_CLASS_PASSWORD=trade2026
export OPTIMUS_ADMIN_KEY=dev-admin
uvicorn main:app --reload --port 8000
```

Run tests and linters:

```bash
pytest
ruff check .
ruff format --check .
mypy .
```

---

## Configuration

All settings are environment variables. See [`.env.example`](.env.example) for
the full list. The most commonly tuned ones:

| Variable                      | Default              | Purpose                                     |
| ----------------------------- | -------------------- | ------------------------------------------- |
| `OPTIMUS_CLASS_PASSWORD`      | *(required)*         | Shared password students enter to join.     |
| `OPTIMUS_ADMIN_KEY`           | *(required)*         | Secret for `/admin/reset?key=…`.            |
| `OPTIMUS_TICK_INTERVAL`       | `1.0`                | Real seconds per in-game hour.              |
| `OPTIMUS_BATTERY_MAX_MWH`     | `50000`              | Battery energy capacity.                    |
| `OPTIMUS_BATTERY_MAX_MW`      | `10000`              | Max charge/discharge rate per hour.         |
| `OPTIMUS_STARTING_CASH`       | `0.0`                | Initial cash per player.                    |
| `OPTIMUS_STARTING_REF_PRICE`  | `45.0`               | Mark-to-market reference price.             |
| `OPTIMUS_DB_PATH`             | `./battery_trader.db` | SQLite location.                           |

> **Security note:** starting with v0.2, the server **refuses to boot** if
> `OPTIMUS_CLASS_PASSWORD` or `OPTIMUS_ADMIN_KEY` is unset. This prevents
> accidental exposure of a public instance with shipped defaults.

---

## How it works

```
                                 ┌────────────────────────┐
                                 │   Background tick loop │
                                 │       (1 Hz)           │
                                 └──────────┬─────────────┘
                                            │
   weather  ─►  pricing  ─►  market coupling  ─►  events  ─►  DA clearing
                                            │
                                            ▼
                             ┌──────────────────────────┐
                             │   GameState (in-memory)  │
                             └──────────┬───────────────┘
                                        │
                        WebSocket broadcast to all clients
                                        │
                                        ▼
              ┌──────────────────────────────────────────────┐
              │  Browser UI: map · chart · trade · leaderboard │
              └──────────────────────────────────────────────┘
```

Player state (cash, battery MWh, trades, SoH) is persisted to SQLite; the
market state lives in memory for speed and is rebuilt on restart.

For a deeper dive, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Educational concepts

Each classroom session walks students through:

1. **Merit-order pricing** — wind and solar push marginal prices down.
2. **The duck curve** — why evenings are expensive and midday is cheap.
3. **Market coupling & congestion rent** — why cheap Danish wind can't always
   reach Germany.
4. **Forecast vs. realized** — markets overreact to surprises (amplifier > 1).
5. **Storage arbitrage** — buy low, sell high, but respect round-trip losses.
6. **Day-ahead vs. intraday** — risk/reward tradeoff between the two venues.
7. **Battery degradation** — every cycle shrinks future capacity.

A lesson-plan draft lives at [`docs/GAMEPLAY.md`](docs/GAMEPLAY.md).

---

## Contributing

Contributions are very welcome! Please read
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup, commit-message
conventions, and PR checklist, and
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards.

Good first issues are tagged
[`good first issue`](https://github.com/timpara/Optimus/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

## Security

To report a vulnerability, please **do not** open a public issue. Instead,
follow the process in [`SECURITY.md`](SECURITY.md).

## License

[MIT](LICENSE) — you may freely use, modify, and redistribute this project,
with or without modifications, including in commercial settings.
