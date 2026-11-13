import pytest
from conftest import FIXTURE_EVAL, FakeLLM
from fastapi.testclient import TestClient

from codeatlas.api.app import create_app
from codeatlas.atlas import CodeAtlas
from codeatlas.embeddings import HashingEmbedder
from codeatlas.eval.dataset import load_eval_set
from codeatlas.eval.runner import evaluate
from codeatlas.llm import LLMError, as_llm_error
from codeatlas.retrieval.retriever import RetrievalConfig

THROTTLED = LLMError("throttled", "fake", "ThrottlingException", "Rate exceeded")


class FailingLLM(FakeLLM):
    """Fails on calls whose prompt contains `trigger` (every call when trigger is empty)."""

    def __init__(self, error: Exception, trigger: str = ""):
        super().__init__("PaymentService.charge retries [1].")
        self.error = error
        self.trigger = trigger

    def complete(self, system, prompt, max_tokens, thinking=None):
        if self.trigger in prompt:
            raise self.error
        return super().complete(system, prompt, max_tokens, thinking)

    def stream(self, system, prompt, max_tokens, thinking=None):
        yield "Partial "
        raise self.error


def make_atlas(settings, repo, llm) -> CodeAtlas:
    atlas = CodeAtlas(settings, embedder=HashingEmbedder(), llm=llm)
    atlas.index(repo)
    return atlas


class _ClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"An error occurred ({code}): {message}")
        self.response = {"Error": {"Code": code, "Message": message}}


@pytest.mark.parametrize(
    ("exc", "kind", "error_class"),
    [
        (_ClientError("ThrottlingException", "Too many requests"), "throttled", "ThrottlingException"),
        (_ClientError("AccessDeniedException", "not authorized"), "access", "AccessDeniedException"),
        (_ClientError("ValidationException", "bad thinking type"), "validation", "ValidationException"),
        (_ClientError("ModelTimeoutException", "took too long"), "timeout", "ModelTimeoutException"),
        (_ClientError("ServiceUnavailableException", "try later"), "unavailable", "ServiceUnavailableException"),
        (type("APITimeoutError", (Exception,), {})("timed out"), "timeout", "APITimeoutError"),
        (type("RateLimitError", (Exception,), {})("429"), "throttled", "RateLimitError"),
        (type("APIConnectionError", (Exception,), {})("refused"), "network", "APIConnectionError"),
        (type("Weird", (Exception,), {})("?"), "other", "Weird"),
    ],
)
def test_provider_errors_are_classified(exc, kind, error_class):
    err = as_llm_error("bedrock:m", exc)
    assert (err.kind, err.error_class, err.provider) == (kind, error_class, "bedrock:m")
    assert err.to_dict()["kind"] == kind


def test_bedrock_sdk_errors_become_llm_errors_and_bugs_pass_through():
    pytest.importorskip("boto3")
    from botocore.exceptions import ClientError

    from codeatlas.llm import BedrockLLM

    llm = BedrockLLM("some-model", region="us-east-1", timeout=30, max_retries=2)
    config = llm._client.meta.config
    assert config.retries["mode"] == "adaptive" and config.retries["total_max_attempts"] == 3  # 1 call + 2 retries
    assert config.read_timeout == 30

    class Stub:
        def __init__(self, exc):
            self.exc = exc

        def converse(self, **_):
            raise self.exc

    llm._client = Stub(ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse"))
    with pytest.raises(LLMError) as info:
        llm.complete("s", "p", 10)
    assert info.value.kind == "throttled" and info.value.message == "slow down"

    llm._client = Stub(KeyError("output"))  # a programming error is not relabelled as a provider error
    with pytest.raises(KeyError):
        llm.complete("s", "p", 10)


def test_api_reports_provider_errors(settings, repo):
    client = TestClient(create_app(atlas=make_atlas(settings, repo, FailingLLM(THROTTLED))))
    resp = client.post("/api/ask", json={"question": "Where is the payment retry logic?", "mode": "hybrid", "k": 5})
    assert resp.status_code == 503
    assert resp.json()["detail"]["kind"] == "throttled"

    stream = client.post("/api/ask/stream", json={"question": "Where is the payment retry logic?", "mode": "hybrid"})
    assert "event: token" in stream.text and "event: error" in stream.text
    assert '"kind": "throttled"' in stream.text and "event: done" not in stream.text


def test_api_stream_reports_unexpected_failures(settings, repo):
    client = TestClient(create_app(atlas=make_atlas(settings, repo, FailingLLM(ValueError("bug")))))
    stream = client.post("/api/ask/stream", json={"question": "Where is the payment retry logic?", "mode": "hybrid"})
    assert "event: error" in stream.text and '"kind": "internal"' in stream.text


def test_failed_llm_calls_are_traced(settings, repo):
    import json

    atlas = make_atlas(settings, repo, FailingLLM(THROTTLED))
    with pytest.raises(LLMError):
        atlas.ask("Where is the payment retry logic?", mode="hybrid", k=5)
    last = json.loads((settings.index_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last["error"]["kind"] == "throttled"


def test_eval_records_errors_per_question_and_keeps_going(settings, repo):
    llm = FailingLLM(THROTTLED, trigger="Which services call the /orders/{id} endpoint?")
    atlas = make_atlas(settings, repo, llm)
    report = evaluate(atlas, load_eval_set(FIXTURE_EVAL), RetrievalConfig.parse("hybrid", k=5), with_answers=True)
    failed = [r for r in report.items if r.error]
    assert [r.id for r in failed] == ["order-endpoint-callers"]
    assert failed[0].error.startswith("throttled: ThrottlingException")
    assert report.summary()["errors"] == 1
    assert sum(1 for r in report.items if r.answer is not None) == 3  # the other questions were answered
