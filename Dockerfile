# ==============================================================================
# MULTI-STAGE DOCKERFILE FOR KPI SOSMED
# ==============================================================================

# --- Stage 1: Base ---
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 -s /bin/bash appuser

# --- Stage 2: Builder ---
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
COPY requirements-dev.txt /tmp/requirements-dev.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements-dev.txt

# --- Stage 3: Runtime (Production) ---
FROM base AS runtime

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
RUN chown -R appuser:appuser /app

# Copy application files
COPY --chown=appuser:appuser app/ /app/app/
COPY --chown=appuser:appuser migrations/ /app/migrations/
COPY --chown=appuser:appuser scripts/ /app/scripts/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# --- Stage 4: Test & Tools ---
FROM runtime AS test

COPY --chown=appuser:appuser tests/ /app/tests/

CMD ["pytest", "-q"]
