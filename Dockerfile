# Multi-stage: build the SPA, then serve it from the FastAPI backend (single container).
# --- stage 1: frontend build ---
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# --- stage 2: backend + bundled SPA ---
FROM python:3.13-slim AS app
WORKDIR /app
COPY pyproject.toml ./
COPY quant_data ./quant_data
COPY quant_web ./quant_web
RUN pip install --no-cache-dir ".[web]"
COPY --from=frontend /fe/dist ./frontend/dist

ENV QUANT_DATA_ROOT=/data \
    AUDIT_DB_URL=sqlite:////data/quant_web_audit.db \
    FRONTEND_DIST=/app/frontend/dist \
    ALLOWED_ORIGINS=http://localhost:8000 \
    JWT_SECRET=change-me-in-prod

EXPOSE 8000
# Gunicorn + Uvicorn workers is the production combo (doc §2.2); single uvicorn is fine for dev.
CMD ["python", "-m", "uvicorn", "quant_web.main:app", "--host", "0.0.0.0", "--port", "8000"]
