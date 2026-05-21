# Multi-stage: build the SPA, then serve it from the FastAPI backend (single container).
# NOTE: build context is the repo ROOT (see docker-compose.yml: context=../../),
# because stage 2 must COPY the shared core lib under packages/.

# --- stage 1: frontend build -----------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY apps/web/frontend/package.json apps/web/frontend/package-lock.json* ./
RUN npm ci
COPY apps/web/frontend/ ./
RUN npm run build

# --- stage 2: backend + bundled SPA ----------------------------------------
FROM python:3.13-slim AS app
WORKDIR /app

# Install the core data layer first (the backend depends on it by name).
COPY packages/quant_data ./packages/quant_data
RUN pip install --no-cache-dir ./packages/quant_data

# Then the web app's backend (the Python distribution lives in apps/web/backend).
COPY apps/web/backend ./apps/web/backend
RUN pip install --no-cache-dir ./apps/web/backend

# Bundled SPA.
COPY --from=frontend /fe/dist ./frontend/dist

ENV QUANT_DATA_ROOT=/data \
    AUDIT_DB_URL=sqlite:////data/quant_web_audit.db \
    FRONTEND_DIST=/app/frontend/dist \
    ALLOWED_ORIGINS=http://localhost:8000 \
    JWT_SECRET=change-me-in-prod

EXPOSE 8000
# Gunicorn + Uvicorn workers is the production combo (doc §2.2); single uvicorn is fine for dev.
CMD ["python", "-m", "uvicorn", "quant_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
