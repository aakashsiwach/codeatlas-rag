"""`codeatlas doctor`: check the environment, index and providers without touching their secrets."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from codeatlas.config import Settings
from codeatlas.llm import LLM, LLMError, LLMNotConfiguredError, build_llm, price_key, thinking_for

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str


def check_python() -> Check:
    v = sys.version_info
    status = OK if (v.major, v.minor) >= (3, 10) else FAIL
    return Check("python", status, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")


def check_index(settings: Settings) -> list[Check]:
    """Open the index the way the CLI does: reports a lock held by another process, and what is indexed."""
    from codeatlas.storage.chunk_store import ChunkStore
    from codeatlas.storage.vector_store import IndexLockedError, VectorStore

    db = settings.index_dir / "codeatlas.db"
    if not db.exists():
        return [Check("index", WARN, f"no index at {settings.index_dir} yet (run `codeatlas index <repo>`)")]
    store = ChunkStore(db)
    try:
        chunks = store.count_chunks()
        embedder = store.get_meta("embedder")
        chunker = store.get_meta("chunker")
        dim = int(store.get_meta("dim") or 0)
    finally:
        store.close()
    try:
        vectors = VectorStore.from_settings(settings, dim or 1)
    except IndexLockedError as exc:
        return [Check("index lock", FAIL, str(exc))]
    except Exception as exc:  # noqa: BLE001 - e.g. an unreachable Qdrant server
        return [Check("vector store", FAIL, f"{type(exc).__name__}: {exc}")]
    try:
        count = vectors.count()
    finally:
        vectors.client.close()
    lock = Check("index lock", OK, "server mode" if settings.qdrant_url else f"{settings.index_dir} is free")
    status = OK if chunks and count == chunks else WARN
    return [lock, Check("index", status, f"{chunks} chunks, {count} vectors, chunker={chunker}, embedder={embedder}")]


def check_embedder(settings: Settings) -> Check:
    from codeatlas.embeddings import build_embedder

    start = time.perf_counter()
    try:
        embedder = build_embedder(settings)
        vector = embedder.embed_query("doctor check")
    except Exception as exc:  # noqa: BLE001 - report any load failure verbatim
        return Check("embedding model", FAIL, f"{settings.embedding_model}: {type(exc).__name__}: {exc}")
    secs = time.perf_counter() - start
    return Check("embedding model", OK, f"{embedder.name}, dim {len(vector)}, loaded in {secs:.1f}s")


def check_reranker(settings: Settings) -> Check:
    if not settings.reranker_model:
        return Check("reranker", OK, "disabled")
    from codeatlas.models import Chunk
    from codeatlas.retrieval.reranker import CrossEncoderReranker

    start = time.perf_counter()
    try:
        chunk = Chunk(id="d", path="A.java", kind="method", text="void run() {}", embed_text="void run() {}", start_line=1, end_line=1)
        CrossEncoderReranker(settings.reranker_model).score("doctor check", [chunk])
    except Exception as exc:  # noqa: BLE001
        return Check("reranker", FAIL, f"{settings.reranker_model}: {type(exc).__name__}: {exc}")
    return Check("reranker", OK, f"{settings.reranker_model}, loaded in {time.perf_counter() - start:.1f}s")


def auth_source(settings: Settings) -> dict[str, Any]:
    """Where provider credentials would come from. Names and profiles only, never secret values."""
    provider = settings.llm_provider.lower()
    if provider == "bedrock":
        try:
            import boto3
        except ImportError:
            return {"auth": "boto3 not installed (pip install 'codeatlas[bedrock]')"}
        try:
            session = boto3.Session(profile_name=settings.aws_profile) if settings.aws_profile else boto3.Session()
        except Exception as exc:  # e.g. ProfileNotFound
            return {"profile": settings.aws_profile, "auth": f"{type(exc).__name__}: {exc}"}
        creds = session.get_credentials()
        return {
            "region": settings.aws_region or session.region_name,
            "auth": getattr(creds, "method", None) if creds else "none found",
            "profile": session.profile_name,
        }
    if provider == "anthropic":
        names = [n for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if os.environ.get(n)]
        return {"auth": f"{names[0]} (set)" if names else "SDK default (profile or none)"}
    if provider == "openai":
        return {"auth": "OPENAI_API_KEY (set)" if os.environ.get("OPENAI_API_KEY") else "OPENAI_API_KEY not set",
                "base_url": settings.llm_base_url}
    return {}


def check_llm_config(settings: Settings) -> Check:
    if settings.llm_provider.lower() == "none":
        return Check("llm config", WARN, "provider is 'none': retrieval works, answers and judge do not")
    if not settings.llm_model:
        return Check("llm config", FAIL, "CODEATLAS_LLM_MODEL is not set")
    info = auth_source(settings)
    priced = "priced" if settings.llm_prices.get(price_key(settings)) else "unpriced (set CODEATLAS_LLM_PRICES)"
    parts = [f"{settings.llm_provider}:{settings.llm_model}", *(f"{k}={v}" for k, v in info.items() if v is not None)]
    parts += [
        f"answer thinking={thinking_for(settings, 'answer') or 'model default'}",
        f"judge thinking={thinking_for(settings, 'judge') or 'model default'}",
        priced,
    ]
    status = FAIL if info.get("auth") == "none found" else (OK if priced == "priced" else WARN)
    return Check("llm config", status, ", ".join(parts))


def check_llm_call(settings: Settings, llm: LLM | None = None) -> Check:
    """ONE minimal call (max 1 output token, thinking as configured for the judge). Billed by the provider."""
    try:
        llm = llm or build_llm(settings)
    except (LLMNotConfiguredError, ImportError) as exc:
        return Check("llm call", FAIL, f"{type(exc).__name__}: {exc}")
    start = time.perf_counter()
    try:
        resp = llm.complete("Reply with one word.", "Say OK.", 1, thinking=thinking_for(settings, "judge"))
    except LLMError as exc:
        return Check("llm call", FAIL, f"{llm.name}: {exc.error_class} ({exc.kind}): {exc.message}")
    secs = time.perf_counter() - start
    return Check(
        "llm call",
        OK,
        f"{llm.name}: stop_reason={resp.stop_reason}, tokens {resp.usage.input_tokens}+{resp.usage.output_tokens}, "
        f"{secs:.1f}s",
    )


def run_checks(settings: Settings, *, llm_call: bool = False, models: bool = True) -> list[Check]:
    checks = [check_python(), *check_index(settings)]
    if models:
        checks += [check_embedder(settings), check_reranker(settings)]
    checks.append(check_llm_config(settings))
    if llm_call:
        checks.append(check_llm_call(settings))
    return checks
