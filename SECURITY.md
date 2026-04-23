# Security Policy

## Supported Versions

Only the latest minor release receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| older   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, report them privately via GitHub's Security Advisories:

1. Go to <https://github.com/timpara/Optimus/security/advisories/new>.
2. Fill out the form with as much detail as you can: affected versions,
   reproduction steps, potential impact, and any suggested fix.

You should receive an acknowledgement within **72 hours**. We aim to issue a
fix and coordinated disclosure within **30 days** for high-severity issues.

## Scope

In scope:

- The FastAPI application (auth, trading endpoints, WebSocket, admin routes).
- The Docker image published to `ghcr.io/timpara/optimus`.
- The build, CI, and release workflows in `.github/workflows/`.

Out of scope (but still appreciated as regular issues):

- Denial-of-service from unrestricted in-game actions — the game is designed
  for trusted classroom use.
- Bugs in the simulation physics that don't affect confidentiality or
  integrity of other players' data.

## Operator guidance

Optimus is built for classroom use on trusted networks. If you expose an
instance to the public internet, you **must**:

- Set a strong, unique `OPTIMUS_CLASS_PASSWORD` and `OPTIMUS_ADMIN_KEY`.
- Terminate TLS in front of the app (reverse proxy with WSS).
- Consider rate-limiting at the ingress layer.
- Back up `/data/battery_trader.db` if per-player history matters.
