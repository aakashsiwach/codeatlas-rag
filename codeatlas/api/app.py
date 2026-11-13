"""HTTP API: ask (JSON or server-sent events), impact analysis, file viewer, indexing and webhook."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from codeatlas import __version__
from codeatlas.api.webhook import changed_paths, verify_signature
from codeatlas.atlas import CodeAtlas
from codeatlas.config import Settings
from codeatlas.ingest.text import split_lines
from codeatlas.ingest.walker import classify
from codeatlas.llm import LLMError, LLMNotConfiguredError

log = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(default=None, description="e.g. hybrid, hybrid+rerank+graph")
    k: int | None = Field(default=None, ge=1, le=30)
    setup: str | None = Field(default=None, description="a named setup, e.g. fast or quality; instead of mode/k")


class IndexRequest(BaseModel):
    repo_path: str | None = None
    force: bool = False


def _sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    try:
        for event in events:
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
    except LLMNotConfiguredError as exc:
        yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
    except LLMError as exc:
        yield f"event: error\ndata: {json.dumps({'detail': str(exc), **exc.to_dict()})}\n\n"
    except Exception:  # noqa: BLE001 - the client must learn the stream failed, not see it just stop
        log.exception("ask stream failed")
        yield f"event: error\ndata: {json.dumps({'detail': 'Internal error while answering', 'kind': 'internal'})}\n\n"


def create_app(settings: Settings | None = None, atlas: CodeAtlas | None = None) -> FastAPI:
    if atlas is None:
        atlas = CodeAtlas(settings or Settings())
    settings = atlas.settings

    app = FastAPI(title="CodeAtlas", version=__version__)
    app.state.atlas = atlas

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        """With CODEATLAS_API_TOKEN set, every /api/* route needs `Authorization: Bearer <token>`.
        The GitHub webhook authenticates with its HMAC signature instead; /health stays open."""
        path = request.url.path
        if (
            settings.api_token
            and request.method != "OPTIONS"
            and path.startswith("/api/")
            and path != "/api/webhook/github"
        ):
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), settings.api_token):
                return JSONResponse({"detail": "Missing or invalid API token"}, status_code=401)
        return await call_next(request)

    # Added after the token check so that it wraps it: CORS headers also reach 401 responses.
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
    )

    def config(mode: str | None, k: int | None):
        try:
            return atlas.config(mode, k)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def resolve(req: AskRequest):
        """(setup name or None, retrieval config) for a request: a named setup, or mode/k."""
        if req.setup is None:
            return None, config(req.mode, req.k)
        if req.mode is not None or req.k is not None:
            raise HTTPException(400, "Pass either a setup or mode/k, not both")
        try:
            return req.setup, atlas.setup_config(req.setup)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def labelled(events, setup: str | None):
        for event in events:
            if event["event"] in ("sources", "done"):
                event["data"]["setup"] = setup
            yield event

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    def active_config() -> dict[str, Any]:
        """The active setup, for the UI settings panel. No secrets: provider and model names only."""
        from codeatlas.retrieval.retriever import FLAGS, MODES, OPTIONS, RetrievalConfig

        default = atlas.config()
        repo = atlas.repo_path
        return {
            "version": __version__,
            "repo": repo.name if repo else None,
            "default_mode": default.name,
            "default_k": default.k,
            "modes": list(MODES),
            "flags": list(FLAGS),
            "options": {name: getattr(default, name) for name in OPTIONS},
            "option_defaults": {name: getattr(RetrievalConfig(), name) for name in OPTIONS},
            "embedder": atlas.store.get_meta("embedder"),
            "chunker": atlas.store.get_meta("chunker"),
            "reranker": settings.reranker_model or None,
            "llm": f"{settings.llm_provider}:{settings.llm_model}" if settings.llm_provider != "none" else None,
            "thresholds": {
                "vector": atlas.engine.threshold_for("vector"),
                "rerank": atlas.engine.threshold_for("rerank"),
            },
            "auth_required": bool(settings.api_token),
            "setups": {name: {"mode": atlas.setup_config(name).name, "k": atlas.setup_config(name).k}
                       for name in settings.setups},
            "default_setup": settings.default_setup,
        }

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return atlas.stats()

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        setup, cfg = resolve(req)
        try:
            return {**atlas.engine.ask(req.question, cfg).to_dict(), "setup": setup}
        except LLMNotConfiguredError as exc:
            raise HTTPException(503, str(exc)) from exc
        except LLMError as exc:
            status = 503 if exc.kind in ("throttled", "unavailable", "timeout", "network") else 502
            raise HTTPException(status, {"detail": str(exc), **exc.to_dict()}) from exc

    @app.post("/api/ask/stream")
    def ask_stream(req: AskRequest) -> StreamingResponse:
        setup, cfg = resolve(req)
        events = labelled(atlas.engine.ask_stream(req.question, cfg), setup)
        return StreamingResponse(_sse(events), media_type="text/event-stream")

    @app.get("/api/impact")
    def impact(symbol: str, depth: int = 3) -> dict[str, Any]:
        targets, hits, report = atlas.impact(symbol, depth=max(1, min(depth, 6)))
        if not targets:
            raise HTTPException(404, f"No class or method matching {symbol!r} in the code graph")
        return {"targets": targets, "dependents": [h.to_dict() for h in hits], "report": report}

    @app.get("/api/file")
    def get_file(
        path: str, start: int | None = Query(default=None, ge=1), end: int | None = Query(default=None, ge=1)
    ) -> dict[str, Any]:
        if start is not None and end is not None and end < start:
            raise HTTPException(400, "end must not be before start")
        repo = atlas.repo_path
        if repo is None:
            raise HTTPException(404, "No repository indexed")
        root = repo.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "File not found")
        # Only files that were indexed can be viewed: never .env, .git/config or other unindexed files.
        if target.relative_to(root).as_posix() not in atlas.store.file_hashes():
            raise HTTPException(404, "File not found")
        lines = split_lines(target.read_text(encoding="utf-8", errors="replace"))
        lo = max(1, start or 1)
        hi = min(len(lines), end or len(lines))
        kind = classify(path)
        return {
            "path": path,
            "language": kind[1] if kind else "",
            "start_line": lo,
            "end_line": hi,
            "total_lines": len(lines),
            "lines": lines[lo - 1 : hi],
        }

    @app.post("/api/index", status_code=202)
    def index(req: IndexRequest, background: BackgroundTasks) -> dict[str, Any]:
        configured = atlas.repo_path
        repo = req.repo_path or (str(configured) if configured else None)
        if not repo:
            raise HTTPException(400, "repo_path is required (or set CODEATLAS_REPO_PATH)")
        if not settings.allow_index_any_path and (
            configured is None or Path(repo).resolve() != Path(configured).resolve()
        ):
            # Indexing an arbitrary server path would also expose it through /api/file.
            raise HTTPException(
                403, "Only the configured repository can be indexed (set CODEATLAS_ALLOW_INDEX_ANY_PATH to allow others)"
            )
        background.add_task(atlas.index, repo, force=req.force)
        return {"status": "accepted", "repo_path": repo}

    @app.get("/api/index/runs")
    def index_runs(limit: int = 20) -> list[dict[str, Any]]:
        return atlas.store.runs(max(1, min(limit, 200)))

    @app.post("/api/webhook/github", status_code=202)
    async def github_webhook(
        request: Request,
        background: BackgroundTasks,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not settings.webhook_secret:
            raise HTTPException(503, "Webhook secret is not configured (CODEATLAS_WEBHOOK_SECRET)")
        body = await request.body()
        if not verify_signature(settings.webhook_secret, body, x_hub_signature_256):
            raise HTTPException(401, "Invalid signature")
        if x_github_event == "ping":
            return {"status": "pong"}
        if x_github_event != "push":
            return {"status": "ignored", "event": x_github_event}
        payload = json.loads(body)
        if settings.webhook_branch and payload.get("ref") != f"refs/heads/{settings.webhook_branch}":
            return {"status": "ignored", "ref": payload.get("ref")}
        paths = changed_paths(payload)
        background.add_task(atlas.sync_from_push, paths)
        return {"status": "accepted", "files": None if paths is None else len(paths)}

    if settings.ui_dir is not None:
        # Mounted last so every API route above takes precedence; html=True serves index.html at /.
        if not (settings.ui_dir / "index.html").is_file():
            raise RuntimeError(f"CODEATLAS_UI_DIR={settings.ui_dir} has no index.html; run `npm run build` in ui/")
        app.mount("/", StaticFiles(directory=settings.ui_dir, html=True), name="ui")
    return app
