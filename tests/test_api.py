import hashlib
import hmac
import json

import pytest
from conftest import JAVA_ROOT, WEBHOOK_SECRET
from fastapi.testclient import TestClient

from codeatlas.api.app import create_app
from codeatlas.api.webhook import changed_paths, verify_signature


@pytest.fixture
def client(atlas) -> TestClient:
    return TestClient(create_app(atlas=atlas))


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_ask(client):
    resp = client.post("/api/ask", json={"question": "Where is the payment retry logic?", "mode": "hybrid", "k": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"][0]["index"] == 1
    assert body["sources"][0]["path"]


def test_ask_rejects_unknown_mode(client):
    resp = client.post("/api/ask", json={"question": "anything", "mode": "hybrid+magic"})
    assert resp.status_code == 400


def test_ask_stream(client):
    resp = client.post("/api/ask/stream", json={"question": "payment retry", "mode": "hybrid", "k": 5})
    assert resp.status_code == 200
    assert "event: sources" in resp.text
    assert "event: done" in resp.text


def test_impact(client):
    body = client.get("/api/impact", params={"symbol": "Customer"}).json()
    assert body["targets"] == ["com.example.shop.customer.Customer"]
    assert any(d["node"] == "com.example.shop.order.Order" for d in body["dependents"])
    assert client.get("/api/impact", params={"symbol": "NoSuchThing"}).status_code == 404


def test_file_viewer(client):
    rel = f"{JAVA_ROOT}/order/OrderController.java"
    body = client.get("/api/file", params={"path": rel, "start": 23, "end": 27}).json()
    assert body["language"] == "java"
    assert body["lines"][0].strip() == "/** Returns a single order by id. */"
    assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 404


def test_file_viewer_serves_only_indexed_files(client, repo, tmp_path):
    (repo / ".env").write_text("CODEATLAS_API_TOKEN=secret", encoding="utf-8")
    (repo / "notes.bin").write_text("not indexed", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    for path in (".env", "notes.bin", str(outside), f"{JAVA_ROOT}/../../../../../outside.txt"):
        assert client.get("/api/file", params={"path": path}).status_code == 404, path
    # Equivalent spellings of an indexed path still work.
    rel = f"{JAVA_ROOT}/order/OrderController.java"
    assert client.get("/api/file", params={"path": f"./{JAVA_ROOT}/order/../order/OrderController.java"}).status_code == 200
    assert client.get("/api/file", params={"path": rel}).json()["total_lines"] > 0


def test_file_viewer_validates_the_line_range(client):
    rel = f"{JAVA_ROOT}/order/OrderController.java"
    assert client.get("/api/file", params={"path": rel, "start": 0}).status_code == 422
    assert client.get("/api/file", params={"path": rel, "end": -5}).status_code == 422
    assert client.get("/api/file", params={"path": rel, "start": 9, "end": 3}).status_code == 400
    body = client.get("/api/file", params={"path": rel, "start": 5, "end": 100000}).json()
    assert body["start_line"] == 5 and body["end_line"] == body["total_lines"]


def test_webhook_reindexes_changed_files(client, atlas, repo):
    rel = f"{JAVA_ROOT}/payment/PaymentService.java"
    path = repo / rel
    path.write_text(path.read_text(encoding="utf-8").replace("three", "four"), encoding="utf-8")
    body = json.dumps(
        {"ref": "refs/heads/main", "commits": [{"added": [], "modified": [rel], "removed": []}]}
    ).encode()

    bad = client.post(
        "/api/webhook/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=0"}
    )
    assert bad.status_code == 401

    ok = client.post(
        "/api/webhook/github", content=body, headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": sign(body)}
    )
    assert ok.status_code == 202
    assert ok.json() == {"status": "accepted", "files": 1}
    run = atlas.store.runs(1)[0]
    assert run["mode"] == "targeted"
    assert run["files_changed"] == 1


def test_webhook_ping(client):
    body = b"{}"
    resp = client.post(
        "/api/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(body)}
    )
    assert resp.json() == {"status": "pong"}


def test_api_token_is_required_when_configured(atlas):
    atlas.settings.api_token = "t0ken"
    client = TestClient(create_app(atlas=atlas))
    assert client.get("/health").status_code == 200  # stays open for health checks
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Basic t0ken"}).status_code == 401
    assert client.get("/api/stats", headers={"Authorization": "Bearer t0ken"}).status_code == 200
    # The webhook keeps its own HMAC authentication.
    body = b"{}"
    ping = client.post(
        "/api/webhook/github", content=body, headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(body)}
    )
    assert ping.json() == {"status": "pong"}


def test_index_endpoint_only_reindexes_the_configured_repo(client, atlas, repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.txt").write_text("token=abc", encoding="utf-8")
    resp = client.post("/api/index", json={"repo_path": str(elsewhere)})
    assert resp.status_code == 403
    assert atlas.repo_path.resolve() == repo.resolve()  # the stored root did not move
    assert client.post("/api/index", json={}).status_code == 202
    assert client.post("/api/index", json={"repo_path": str(repo)}).status_code == 202

    atlas.settings.allow_index_any_path = True
    assert TestClient(create_app(atlas=atlas)).post("/api/index", json={"repo_path": str(elsewhere)}).status_code == 202


def test_serve_refuses_public_host_without_a_token(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from codeatlas.cli import _is_loopback, app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODEATLAS_API_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 2 and "Refusing to serve" in result.output
    assert _is_loopback("127.0.0.1") and _is_loopback("::1") and _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("192.168.1.5")


def test_webhook_helpers():
    assert verify_signature("k", b"x", "sha256=" + hmac.new(b"k", b"x", hashlib.sha256).hexdigest())
    assert not verify_signature("k", b"x", None)
    payload = {"commits": [{"added": ["a.java"], "modified": ["b.md"], "removed": ["c.java"]}, {"modified": ["a.java"]}]}
    assert changed_paths(payload) == ["a.java", "b.md", "c.java"]
    assert changed_paths({"commits": [{}] * 20}) is None


def test_webhook_rejects_missing_or_malformed_signatures(client, atlas):
    body = b'{"ref": "refs/heads/main", "commits": []}'
    headers = {"X-GitHub-Event": "push"}
    assert client.post("/api/webhook/github", content=body, headers=headers).status_code == 401
    for sig in ("sha1=" + sign(body)[7:], sign(body).upper(), sign(body + b" ")):
        resp = client.post("/api/webhook/github", content=body, headers={**headers, "X-Hub-Signature-256": sig})
        assert resp.status_code == 401, sig
    atlas.settings.webhook_secret = None
    unconfigured = TestClient(create_app(atlas=atlas))
    assert unconfigured.post("/api/webhook/github", content=body, headers={**headers, "X-Hub-Signature-256": sign(body)}).status_code == 503


def test_config_endpoint_describes_the_active_setup_without_secrets(client, atlas):
    atlas.settings.api_token = None
    body = client.get("/api/config").json()
    assert body["default_mode"] == atlas.config().name and body["default_k"] == atlas.settings.default_k
    assert body["embedder"] == "hashing-512" and body["chunker"] == "ast"
    assert set(body["options"]) >= {"demote_tests", "graph_slots", "rerank_pool"}
    assert body["thresholds"]["vector"] == atlas.settings.min_vector_similarity
    assert body["auth_required"] is False and "api_token" not in body


def test_offline_llm_streams_cited_answers_without_a_model(settings, repo):
    from codeatlas.atlas import CodeAtlas
    from codeatlas.embeddings import HashingEmbedder
    from codeatlas.llm import OfflineLLM, build_llm

    assert isinstance(build_llm(settings.model_copy(update={"llm_provider": "fake"})), OfflineLLM)
    atlas = CodeAtlas(settings.model_copy(update={"llm_provider": "fake"}), embedder=HashingEmbedder())
    atlas.index(repo)
    client = TestClient(create_app(atlas=atlas))
    stream = client.post("/api/ask/stream", json={"question": "Where is the payment retry logic?", "mode": "hybrid"})
    assert stream.text.count("event: token") > 3 and "event: done" in stream.text
    done = client.post("/api/ask", json={"question": "Where is the payment retry logic?", "mode": "hybrid"}).json()
    assert [c["index"] for c in done["citations"]][:1] == [1] and not done["refused"]


def test_ask_takes_a_named_setup_and_reports_it_with_timings(client, atlas):
    atlas.settings.setups = {"fast": {"mode": "vector", "k": 3}, "quality": {"mode": "hybrid+graph", "k": 4}}
    body = client.get("/api/config").json()
    assert body["setups"] == {"fast": {"mode": "vector", "k": 3}, "quality": {"mode": "hybrid+graph", "k": 4}}
    assert body["default_setup"] == "fast"

    resp = client.post("/api/ask", json={"question": "Where is the payment retry logic?", "setup": "quality"})
    assert resp.status_code == 200
    answer = resp.json()
    assert (answer["setup"], answer["config"]) == ("quality", "hybrid+graph")
    assert len(answer["sources"]) <= 4 + 1  # k blocks plus the graph report
    assert 0 <= answer["timings_ms"]["retrieval_ms"] <= answer["timings_ms"]["total_ms"]

    stream = client.post("/api/ask/stream", json={"question": "payment retry", "setup": "fast"})
    events = [data for name, data in _events(stream.text) if name != "progress"]
    sources, done = events[0], events[-1]
    assert sources["setup"] == "fast" and sources["retrieval_ms"] >= 0
    assert (done["setup"], done["config"]) == ("fast", "vector") and done["timings_ms"]["total_ms"] >= 0

    without = client.post("/api/ask", json={"question": "payment retry", "mode": "hybrid"}).json()
    assert without["setup"] is None and without["config"] == "hybrid"


def test_ask_rejects_unknown_setups_and_setup_with_mode(client):
    unknown = client.post("/api/ask", json={"question": "anything", "setup": "turbo"})
    assert unknown.status_code == 400 and "fast" in unknown.json()["detail"]
    both = client.post("/api/ask/stream", json={"question": "anything", "setup": "fast", "mode": "hybrid"})
    assert both.status_code == 400


def test_serves_the_built_ui_when_configured(atlas, tmp_path):
    ui = tmp_path / "dist"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>CodeAtlas</title>", encoding="utf-8")
    (ui / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    atlas.settings.ui_dir = ui
    atlas.settings.api_token = "s3cret"
    try:
        client = TestClient(create_app(atlas=atlas))
        assert "<title>CodeAtlas</title>" in client.get("/").text
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/api/config").status_code == 401  # the UI is public; the API still needs the token
        auth = {"Authorization": "Bearer s3cret"}
        assert client.get("/api/config", headers=auth).json()["default_setup"] == "fast"
    finally:
        atlas.settings.ui_dir = None
        atlas.settings.api_token = None


def test_ui_dir_without_a_build_fails_fast(atlas, tmp_path):
    atlas.settings.ui_dir = tmp_path
    try:
        with pytest.raises(RuntimeError, match="npm run build"):
            create_app(atlas=atlas)
    finally:
        atlas.settings.ui_dir = None


def _events(text: str) -> list[tuple[str, dict]]:
    names = [line[7:] for line in text.splitlines() if line.startswith("event: ")]
    data = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
    return list(zip(names, data))


def test_stream_opens_at_once_and_sends_progress_while_retrieving(client, atlas, monkeypatch):
    import time

    atlas.settings.sse_heartbeat_s = 0.05
    real = atlas.engine.ask_stream

    def slow(question, cfg):  # a reranker that takes a while before the sources are ready
        time.sleep(0.4)
        yield from real(question, cfg)

    monkeypatch.setattr(atlas.engine, "ask_stream", slow)
    try:
        resp = client.post("/api/ask/stream", json={"question": "payment retry", "setup": "fast"})
    finally:
        atlas.settings.sse_heartbeat_s = 5.0
    assert resp.headers["cache-control"] == "no-cache"
    events = _events(resp.text)
    names = [name for name, _ in events]
    assert names[0] == "progress" and events[0][1] == {"stage": "retrieving", "elapsed_ms": events[0][1]["elapsed_ms"]}
    before_sources = names[: names.index("sources")]
    assert set(before_sources) == {"progress"} and len(before_sources) >= 4
    elapsed = [data["elapsed_ms"] for name, data in events if name == "progress"]
    assert elapsed == sorted(elapsed) and elapsed[-1] >= 300
    assert names[-1] == "done" and events[-1][1]["setup"] == "fast"


def test_stream_reports_errors_raised_behind_the_heartbeat(client, atlas, monkeypatch):
    from codeatlas.llm import LLMError

    def failing(question, cfg):
        yield {"event": "sources", "data": {"sources": [], "confidence": 0.9, "confidence_source": "vector"}}
        raise LLMError("throttled", "bedrock:m", "ThrottlingException", "slow down")

    monkeypatch.setattr(atlas.engine, "ask_stream", failing)
    names_and_data = _events(client.post("/api/ask/stream", json={"question": "anything"}).text)
    assert [n for n, _ in names_and_data] == ["progress", "sources", "error"]
    assert names_and_data[-1][1]["kind"] == "throttled"
