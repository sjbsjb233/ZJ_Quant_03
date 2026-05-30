# data-keeper — the single writer of the data lake (write-command API +
# in-process JobRunner + scheduler). Mirrors apps/factor/Dockerfile but ships
# only quant_data (no quant_factor) plus the ingest source connectors.
# Build context = repo root so we can COPY packages/ and the app.
FROM python:3.11-slim AS base
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install the core data layer first, then the keeper service (with the ingest
# extra: akshare primary / tushare fallback).
COPY packages/quant_data /app/packages/quant_data
COPY apps/data-keeper /app/apps/data-keeper

RUN pip install \
    ./packages/quant_data \
    "./apps/data-keeper[ingest]"

# Single container runs the write-command API + in-process JobRunner + scheduler.
# To later split heavy writes into a dedicated worker container (V3 §4.2.1),
# override with:  command: ["python","-m","keeper_service.jobs.dispatcher"]
EXPOSE 8300
CMD ["uvicorn", "keeper_service.main:app", "--host", "0.0.0.0", "--port", "8300"]
