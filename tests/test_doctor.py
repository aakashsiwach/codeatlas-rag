from conftest import FakeLLM
from typer.testing import CliRunner

from codeatlas.atlas import CodeAtlas
from codeatlas.cli import app
from codeatlas.config import Settings
from codeatlas.doctor import FAIL, OK, WARN, check_llm_call, check_llm_config, run_checks
from codeatlas.embeddings import HashingEmbedder
from codeatlas.llm import LLMError


def _embedded_settings(tmp_path, **extra) -> Settings:
    values = {
        "index_dir": tmp_path / "idx",
        "qdrant_url": None,
        "embedding_provider": "hashing",
        "reranker_model": "",
        "llm_provider": "none",
    }
    return Settings(_env_file=None, **{**values, **extra})


def test_doctor_reports_index_and_lock(tmp_path, repo):
    settings = _embedded_settings(tmp_path)
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["index"].status == WARN and "no index" in checks["index"].detail
    assert checks["python"].status == OK and checks["embedding model"].status == OK

    atlas = CodeAtlas(settings, embedder=HashingEmbedder())
    atlas.index(repo)
    locked = {c.name: c for c in run_checks(settings)}  # atlas still holds the embedded index
    assert locked["index lock"].status == FAIL and "--index-dir" in locked["index lock"].detail
    atlas.vectors.client.close()
    free = {c.name: c for c in run_checks(settings)}
    assert free["index lock"].status == OK and free["index"].status == OK
    assert free["llm config"].status == WARN  # provider 'none'


def test_doctor_llm_config_never_prints_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-very-secret")
    settings = _embedded_settings(tmp_path, llm_provider="anthropic", llm_model="m")
    check = check_llm_config(settings)
    assert "ANTHROPIC_API_KEY (set)" in check.detail and "sk-very-secret" not in check.detail
    assert "unpriced" in check.detail and check.status == WARN
    assert check_llm_config(settings.model_copy(update={"llm_model": None})).status == FAIL


def test_doctor_llm_call_reports_success_and_the_exact_error_class(tmp_path):
    settings = _embedded_settings(tmp_path, llm_provider="bedrock", llm_model="m", judge_thinking="disabled")
    fake = FakeLLM("OK")
    ok = check_llm_call(settings, fake)
    assert ok.status == OK and "stop_reason=end_turn" in ok.detail and fake.thinking == ["disabled"]

    class Denied(FakeLLM):
        name = "bedrock:m"

        def complete(self, system, prompt, max_tokens, thinking=None):
            assert max_tokens == 1
            raise LLMError("access", "bedrock:m", "AccessDeniedException", "model not available for this account")

    failed = check_llm_call(settings, Denied(""))
    assert failed.status == FAIL
    assert failed.detail == "bedrock:m: AccessDeniedException (access): model not available for this account"


def test_doctor_cli_exit_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEATLAS_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEATLAS_RERANKER_MODEL", "")
    monkeypatch.setenv("CODEATLAS_LLM_PROVIDER", "none")
    monkeypatch.delenv("CODEATLAS_QDRANT_URL", raising=False)
    result = CliRunner().invoke(app, ["--index-dir", str(tmp_path / "idx"), "doctor", "--skip-models"])
    assert result.exit_code == 0, result.output
    assert "python" in result.output and "llm config" in result.output


def test_doctor_accepts_the_fake_provider_without_a_model(tmp_path):
    from codeatlas.config import Settings
    from codeatlas.doctor import OK, check_llm_config

    check = check_llm_config(Settings(_env_file=None, index_dir=tmp_path, llm_provider="fake", llm_model=""))
    assert check.status == OK and "fake:offline" in check.detail
