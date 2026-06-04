# obs-sentinel — 第一层 · 心跳/基建哨兵 (可观测层重构方案 §5).
# Minimal-privilege bypass container: reads docker.sock:ro + host partitions:ro,
# self-detects L0/D1–D4/D7/D13–D15, POSTs v2 signals to web-backend, AND serves a
# read-only HTTP/SSE status frame on :8400 (绕开 backend 直推前端). stdlib only →
# no pip deps, tiny image, robust (it must keep observing even when all is on fire).
# Build context = repo root (compose sets it), mirroring the other service images.
FROM python:3.13-slim AS app
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY apps/obs-sentinel ./apps/obs-sentinel
RUN pip install ./apps/obs-sentinel

EXPOSE 8400
CMD ["python", "-m", "obs_sentinel"]
