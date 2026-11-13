"""`codeatlas demo`: repository preparation, UI build decisions and the command itself, offline."""

import subprocess

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from codeatlas import demo
from codeatlas.cli import app


class FakeRun:
    """Records commands instead of running them."""

    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.on_call:
            self.on_call(cmd, kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="500158f\n", stderr="")


def test_existing_checkout_is_left_alone(tmp_path):
    run, said = FakeRun(), []
    demo.ensure_repo(tmp_path, said.append, run=run)
    assert run.calls == [["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"]]
    assert "at 500158f" in said[0] and "left unchanged" not in said[0]


def test_missing_checkout_is_cloned_at_the_evaluated_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(demo.shutil, "which", lambda name: "/usr/bin/git")
    target, run = tmp_path / "repos" / "spring-petclinic", FakeRun()
    demo.ensure_repo(target, lambda _: None, run=run)
    assert run.calls == [
        ["git", "clone", "--quiet", demo.PETCLINIC_URL, str(target)],
        ["git", "-C", str(target), "checkout", "--quiet", demo.PETCLINIC_REF],
    ]


def test_ui_is_built_only_when_needed(tmp_path, monkeypatch):
    source = tmp_path / "ui"
    source.mkdir()
    (source / "package.json").write_text("{}", encoding="utf-8")
    said = []

    monkeypatch.setattr(demo.shutil, "which", lambda name: None)
    assert demo.build_ui(said.append, source=source, run=FakeRun()) is None  # no npm
    assert "serving the API only" in said[-1]

    def build(cmd, kwargs):
        if cmd[1:] == ["run", "build"]:
            (source / "dist").mkdir()
            (source / "dist" / "index.html").write_text("<title>CodeAtlas</title>", encoding="utf-8")

    monkeypatch.setattr(demo.shutil, "which", lambda name: "npm")
    run = FakeRun(build)
    assert demo.build_ui(said.append, source=source, run=run) == source / "dist"
    assert run.calls == [["npm", "ci", "--no-audit", "--no-fund"], ["npm", "run", "build"]]
    run = FakeRun()
    assert demo.build_ui(said.append, source=source, run=run) == source / "dist" and run.calls == []  # already built


def test_demo_command_indexes_and_serves_with_the_fake_llm(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEATLAS_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEATLAS_QDRANT_URL", ":memory:")
    monkeypatch.setenv("CODEATLAS_RERANKER_MODEL", "")
    monkeypatch.setenv("CODEATLAS_LLM_PROVIDER", "bedrock")  # the demo overrides it
    served = {}

    def fake_run(application, host, port):
        served.update(app=application, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(app, ["--index-dir", str(tmp_path / "idx"), "demo", "--repo", str(repo), "--no-ui", "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert "full index" in result.output and "LLM fake:offline" in result.output
    assert (served["host"], served["port"]) == ("127.0.0.1", 8123)

    client = TestClient(served["app"])
    config = client.get("/api/config").json()
    assert config["llm"] == "fake:offline" and config["repo"] == repo.name
    answer = client.post("/api/ask", json={"question": "Where is the payment retry logic?", "setup": "fast"}).json()
    assert "Offline demo answer" in answer["answer"] or answer["refused"]
