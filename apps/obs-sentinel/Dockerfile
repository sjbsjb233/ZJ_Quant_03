# obs-sentinel — 进程哨兵 / 外部观察 (可观测层重构方案 §4.6).
# Minimal-privilege bypass container: reads docker.sock:ro, POSTs OOM/restart/
# health/mem signals to web-backend. stdlib only → no pip deps, tiny image.
# Build context = repo root (compose sets it), mirroring the other service images.
FROM python:3.13-slim AS app
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY apps/obs-sentinel ./apps/obs-sentinel
RUN pip install ./apps/obs-sentinel

CMD ["python", "-m", "obs_sentinel"]
