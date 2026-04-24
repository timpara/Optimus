# ─────────────────────────────────────────────────────────────────────────────
# Optimus — Battery Trader Sim
# Multi-stage build → small final image, no build tooling at runtime.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install the package into an isolated venv so we can copy it cleanly into
# the final stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY main.py ./
# The `optimus` package is declared in pyproject.toml (packages = ["optimus"]),
# so `pip install .` needs the source tree present — otherwise the build
# succeeds but the resulting venv is missing the `optimus.config` /
# `optimus.constants` modules that main.py imports at startup.
COPY optimus ./optimus/
# Build + install — this resolves FastAPI, uvicorn, aiosqlite.
RUN pip install --upgrade pip && pip install .

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# OCI image labels — picked up by docker inspect, ghcr, etc.
ARG VERSION="dev"
LABEL org.opencontainers.image.title="Optimus" \
      org.opencontainers.image.description="Battery Trader Sim — multiplayer energy trading educational game" \
      org.opencontainers.image.source="https://github.com/timpara/Optimus" \
      org.opencontainers.image.url="https://github.com/timpara/Optimus" \
      org.opencontainers.image.documentation="https://github.com/timpara/Optimus#readme" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    OPTIMUS_DB_PATH=/data/battery_trader.db

# Non-root user.
RUN groupadd --gid 1000 optimus && \
    useradd --uid 1000 --gid optimus --create-home optimus && \
    mkdir -p /data && chown optimus:optimus /data

WORKDIR /app

# Bring the pre-built venv and the application files over.
COPY --from=builder /opt/venv /opt/venv
COPY --chown=optimus:optimus main.py index.html ./

USER optimus

EXPOSE 8000

# Hit the lightweight /health endpoint — no DB access, no side effects.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
