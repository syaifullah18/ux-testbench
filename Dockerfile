# Production image. Gunicorn serves the app; Caddy in front terminates TLS and serves the
# separate usercontent origin (see docker-compose.yml and docs/operations.md).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# sqlite3 is here for the backup script, curl for the container health check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt gunicorn

COPY testbench/ ./testbench/
COPY projects/example/ ./projects/example/
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY tailwind.config.js README.md LICENSE ./

# The app never writes inside the image; everything mutable lives in the DATA_DIR volume.
RUN useradd --create-home --uid 10001 testbench \
    && mkdir -p /data && chown testbench:testbench /data
USER testbench

ENV DATA_DIR=/data \
    TESTBENCH_MODE=public \
    PORT=8000

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Two workers is plenty for this scale; SQLite is in WAL mode, so reads do not block the writer.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "--access-logfile", "-", \
     "--forwarded-allow-ips", "*", "testbench:create_app()"]
