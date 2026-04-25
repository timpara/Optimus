# Gameplay guide (for instructors)

gridplay is designed for a 60–90 minute classroom session of up to ~40
students. This guide outlines a suggested flow.

## Before class

1. Deploy a server (see [`DEPLOYMENT.md`](DEPLOYMENT.md)). A small cloud VM
   or a laptop on the classroom Wi-Fi is plenty.
2. Pick a `GRIDPLAY_CLASS_PASSWORD` — share it on the board.
3. Optionally pre-set `GRIDPLAY_TICK_INTERVAL=2.0` to give beginners more
   breathing room (2 real seconds per in-game hour).

## Session plan

### 0–10 min · Context

Briefly cover:

- Why Europe has five coupled spot markets with different price levels.
- What a grid-scale battery does (arbitrage + grid services).
- Merit-order pricing, 20 seconds of math.

### 10–25 min · Guided exploration

Have students log in and complete the built-in tutorial (click the `?` icon).
Then walk through the UI as a group:

- The **map** — price color coding, flow arrows, congested cables pulse red.
- The **weather forecast panel** — 6-hour outlook, per-zone noise.
- The **battery gauge** — SoC, SoH, charge taper.
- The **trade execution** controls and the **day-ahead** panel.

### 25–75 min · Free play

Students trade. Every 10–15 minutes, pause (admin endpoint) and have a
student explain what they're doing and why. Example prompts:

1. "Denmark just had a storm — what's the cheapest zone now? Why isn't
   Germany's price the same?"
2. "Midday solar is crushing French prices — who's buying? What happens
   at 18:00?"
3. "The carbon price just spiked. Which zones moved most? Why Poland?"

### 75–90 min · Debrief

Show the leaderboard. Ask the top three traders to describe their strategy.
Discuss what's realistic about the simulation and what isn't.

## Admin controls

- Pause / resume the game: `POST /admin/pause?key=…` and `/admin/resume`.
- Change speed: `POST /admin/speed?key=…&speed=2.0`.
- Full reset: `POST /admin/reset?key=…`.

## Assessment ideas

- Students hand in a 1-page report on their best and worst trade.
- Pair exercise: explain to a partner why the `DE-DK` interconnector was
  congested at hour X.
- Spreadsheet exercise: export the trade log and compute Sharpe ratio.
