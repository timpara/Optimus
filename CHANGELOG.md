# Changelog

## [0.3.0] - 2026-04-26 — Project renamed: Optimus → gridplay

### BREAKING

- **Project, Python package, Docker image, and env-var prefix renamed.**
  - Repository: `github.com/timpara/Optimus` → `github.com/timpara/gridplay`
  - Container image: `ghcr.io/timpara/optimus` → `ghcr.io/timpara/gridplay`
  - Python package: `from optimus.config …` → `from gridplay.config …`
  - Docker volume: `optimus-data` → `gridplay-data`
  - OS user/group inside the container: `optimus` → `gridplay` (uid/gid 1000 preserved, so existing volume permissions still work).

- **Environment variable prefix migration:**

  | Old                            | New                              |
  | ------------------------------ | -------------------------------- |
  | `OPTIMUS_CLASS_PASSWORD`       | `GRIDPLAY_CLASS_PASSWORD`        |
  | `OPTIMUS_ADMIN_KEY`            | `GRIDPLAY_ADMIN_KEY`             |
  | `OPTIMUS_TICK_INTERVAL`        | `GRIDPLAY_TICK_INTERVAL`         |
  | `OPTIMUS_BATTERY_MAX_MWH`      | `GRIDPLAY_BATTERY_MAX_MWH`       |
  | `OPTIMUS_BATTERY_MAX_MW`       | `GRIDPLAY_BATTERY_MAX_MW`        |
  | `OPTIMUS_BATTERY_START_MWH`    | `GRIDPLAY_BATTERY_START_MWH`     |
  | `OPTIMUS_STARTING_CASH`        | `GRIDPLAY_STARTING_CASH`         |
  | `OPTIMUS_STARTING_REF_PRICE`   | `GRIDPLAY_STARTING_REF_PRICE`    |
  | `OPTIMUS_TRADE_RATE_LIMIT`     | `GRIDPLAY_TRADE_RATE_LIMIT`      |
  | `OPTIMUS_TRADE_RATE_WINDOW`    | `GRIDPLAY_TRADE_RATE_WINDOW`     |
  | `OPTIMUS_DB_PATH`              | `GRIDPLAY_DB_PATH`               |

- **Admin key is no longer accepted via the URL.** The `/admin/*` endpoints
  now read the secret from the `X-Admin-Key` HTTP header. The legacy `?key=…`
  query parameter and JSON `key` body field are still accepted for one minor
  version (silent fallback) and will be removed in v0.4.

- **WebSocket authentication is no longer accepted via the URL.** Clients
  must now present the bearer token via the `Sec-WebSocket-Protocol` header
  as `bearer.<token>`. The frontend has been updated; legacy `?token=…`
  remains as a fallback for one minor version.

### Security

- Admin key and player tokens are no longer written to access logs (they are
  no longer in URLs). Uvicorn's default access log is disabled in the Docker
  image.
- `hmac.compare_digest` is used for class-password and admin-key comparisons
  (constant time).

### Added

- Public `/impressum` (§ 5 DDG) and `/datenschutz` (DSGVO) pages, reachable
  without login. Sources live in `docs/legal/` as Markdown.
- Login screen carries an explicit educational-purpose disclaimer (EN+DE)
  and links to the legal pages.
- Leaflet map now displays the required OpenStreetMap and CARTO attribution.

### Migration

1. Update your `.env` to use the `GRIDPLAY_` prefix (table above).
2. If you used `docker compose`, re-run `docker compose up -d --build` after
   pulling the new code; the service and volume names have changed.
3. If you used the GHCR image, switch to `ghcr.io/timpara/gridplay:latest`.
4. Update operator scripts that called `/admin/*?key=…` to send the secret
   in an `X-Admin-Key` header instead.
5. Existing SQLite data in `/data/battery_trader.db` remains compatible —
   no schema changes.
6. Fill the placeholders in `docs/legal/impressum.md` (name, address, email)
   before exposing the instance to students.

## [0.1.1](https://github.com/timpara/gridplay/compare/v0.1.0...v0.1.1) (2026-04-22)


### Bug Fixes

* clear game_sessions on admin reset ([#1](https://github.com/timpara/gridplay/issues/1)) ([acd6ad0](https://github.com/timpara/gridplay/commit/acd6ad0e4af4ce19ae97239d6e32df385d88ddd9))
