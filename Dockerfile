# ─────────────────────────────────────────────────────────────────────────────
# Optimus — Battery Trader Sim
# Lightweight production image (python:3.12-slim, ~150 MB)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
# so container logs appear in real time.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user for security.
RUN groupadd --gid 1000 optimus && \
    useradd --uid 1000 --gid optimus --create-home optimus

# Set up the working directory.
WORKDIR /app

# Install Python dependencies first (layer caching: deps change less often
# than application code, so this layer is cached across rebuilds).
COPY pyproject.toml .
RUN pip install --no-cache-dir . && \
    rm -rf /root/.cache

# Copy application files.
COPY main.py index.html ./

# Create the data directory for the SQLite database and give the non-root
# user ownership. This path is the default for OPTIMUS_DB_PATH in the
# container and should be mounted as a Docker volume for persistence.
RUN mkdir -p /data && chown optimus:optimus /data

# Default database path inside the container (overridable via env var).
ENV OPTIMUS_DB_PATH=/data/battery_trader.db

# Switch to non-root user.
USER optimus

# Expose the application port.
EXPOSE 8000

# Health check — hit the root page every 30s to verify the app is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Run with uvicorn in production mode (no --reload).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
