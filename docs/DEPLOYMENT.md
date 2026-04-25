# Deployment

## Local / classroom (Docker)

The fastest path for most users.

```bash
git clone https://github.com/timpara/gridplay.git
cd gridplay
cp .env.example .env
# edit .env and set GRIDPLAY_CLASS_PASSWORD + GRIDPLAY_ADMIN_KEY
docker compose up -d
```

gridplay will be on <http://localhost:8000>. The SQLite DB lives in the
`gridplay-data` named volume — your classroom state survives container
restarts.

To wipe everything and start fresh:

```bash
docker compose down -v
```

## Single-image deployment

```bash
docker run -d --name gridplay \
  -p 8000:8000 \
  -e GRIDPLAY_CLASS_PASSWORD="change-me" \
  -e GRIDPLAY_ADMIN_KEY="also-change-me" \
  -v gridplay-data:/data \
  --restart unless-stopped \
  ghcr.io/timpara/gridplay:latest
```

Tags:

- `latest` — most recent stable release.
- `0.1` or `0.1.1` — pinned minor/patch.
- `sha-<abc1234>` — exact commit.

## Running behind a reverse proxy (TLS + WSS)

Example Caddy config:

```
class.example.com {
  reverse_proxy localhost:8000
}
```

Caddy automatically upgrades WebSocket traffic to WSS. For nginx, ensure
`Upgrade` and `Connection` headers are forwarded.

## Environment variables

See [`.env.example`](../.env.example) for the canonical list.

## Backups

Back up `battery_trader.db` (volume: `gridplay-data`, path: `/data`) if you
want to preserve per-player state across redeployments.
