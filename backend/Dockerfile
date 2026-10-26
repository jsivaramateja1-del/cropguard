# CropGuard backend — portable image.
#
# Deliberately torch-free: the served artifact is ONNX, and the PyTorch stack needs
# ~660 MB against this service's 512 MB budget. See docs/architecture.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the container healthcheck only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY . .

# Run as a non-root user; uploads live in a directory it owns.
RUN useradd --create-home --uid 10001 cropguard \
 && mkdir -p /app/uploads \
 && chown -R cropguard:cropguard /app
USER cropguard

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
