# factor-api / factor-worker (same image, role selected via FACTOR_ROLE).
# Build context = repo root so we can COPY both packages and the app.
FROM python:3.11-slim AS base
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install editable copies of the two libraries this service depends on,
# then the service itself. Order matters — packages first.
COPY packages/quant_data /app/packages/quant_data
COPY packages/quant_factor /app/packages/quant_factor
COPY apps/factor /app/apps/factor

RUN pip install \
    ./packages/quant_data \
    ./packages/quant_factor \
    ./apps/factor

# Default to api role; compose overrides command for worker role.
EXPOSE 8100
CMD ["uvicorn", "factor_service.main:app", "--host", "0.0.0.0", "--port", "8100"]
