# web-backend — pure API + 读控制台 + 鉴权 + 审计 + 日志中枢 (容器化重构 V3 §5/§8.3).
# No frontend stage, no bundled SPA: the web-frontend nginx container serves the
# SPA and reverse-proxies /api here. Build context = repo root (compose sets it)
# so we can COPY the shared core lib under packages/.
FROM python:3.13-slim AS app
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install the core data layer first (the read console depends on it by name),
# then the web backend.
COPY packages/quant_data ./packages/quant_data
RUN pip install ./packages/quant_data

COPY apps/web/backend ./apps/web/backend
RUN pip install ./apps/web/backend

ENV QUANT_DATA_ROOT=/data \
    AUDIT_DB_URL=sqlite:////state/quant_web_audit.db

EXPOSE 8000
CMD ["uvicorn", "quant_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
