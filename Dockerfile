# syntax=docker/dockerfile:1

# ---- builder: install the package into an isolated prefix ----
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && pip install --prefix=/install .

# ---- runtime: slim image, non-root ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy the installed site-packages + entry points from the builder.
COPY --from=builder /install /usr/local

RUN useradd --create-home --uid 10001 app
USER app
WORKDIR /home/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

# Serve the fleet API (seeds the 3-drone demo on startup).
CMD ["uvicorn", "sentinelswarm.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
