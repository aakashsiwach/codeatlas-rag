FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY codeatlas ./codeatlas

ARG EXTRAS=local,anthropic,openai,bedrock
# CPU-only torch keeps the image small; sentence-transformers runs fine on CPU for this workload.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu ".[${EXTRAS}]"

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000
CMD ["codeatlas", "serve", "--host", "0.0.0.0", "--port", "8000"]
