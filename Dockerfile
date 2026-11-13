# syntax=docker/dockerfile:1

# --- web UI: static build served by the API ------------------------------------------------------
FROM node:26-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/index.html ui/tsconfig.json ui/vite.config.ts ./
COPY ui/src ./src
RUN npx vite build

# --- app: the API and UI (docker compose uses this target) ---------------------------------------
FROM python:3.15-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf-cache \
    CODEATLAS_UI_DIR=/app/ui

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE requirements-lock.txt ./
COPY codeatlas ./codeatlas

ARG EXTRAS=local,anthropic,openai,bedrock
# CPU-only torch keeps the image small; sentence-transformers runs fine on CPU for this workload.
# Pinned by requirements-lock.txt (constraints: versions only).
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -c requirements-lock.txt ".[${EXTRAS}]"

COPY --from=ui /ui/dist /app/ui

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["codeatlas", "serve", "--host", "0.0.0.0", "--port", "8000"]

# --- deploy: app + the PetClinic index and both models baked in (no downloads at start-up) -------
FROM app AS deploy

ARG PETCLINIC_REPO=https://github.com/spring-projects/spring-petclinic
ARG PETCLINIC_REF=500158f
ENV CODEATLAS_INDEX_DIR=/data/index \
    CODEATLAS_REPO_PATH=/data/spring-petclinic \
    CODEATLAS_DEFAULT_MODE=vector

RUN git clone --filter=blob:none "${PETCLINIC_REPO}" /data/spring-petclinic \
    && git -C /data/spring-petclinic checkout --quiet "${PETCLINIC_REF}" \
    && codeatlas index /data/spring-petclinic \
    && python -c "from codeatlas.config import Settings; from sentence_transformers import CrossEncoder; CrossEncoder(Settings().reranker_model)"
