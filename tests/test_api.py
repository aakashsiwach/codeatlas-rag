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


def test_webhook_helpers():
    assert verify_signature("k", b"x", "sha256=" + hmac.new(b"k", b"x", hashlib.sha256).hexdigest())
    assert not verify_signature("k", b"x", None)
    payload = {"commits": [{"added": ["a.java"], "modified": ["b.md"], "removed": ["c.java"]}, {"modified": ["a.java"]}]}
    assert changed_paths(payload) == ["a.java", "b.md", "c.java"]
    assert changed_paths({"commits": [{}] * 20}) is None
