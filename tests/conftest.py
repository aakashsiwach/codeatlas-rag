import shutil
from pathlib import Path

import pytest

from codeatlas.atlas import CodeAtlas
from codeatlas.config import Settings
from codeatlas.embeddings import HashingEmbedder
from codeatlas.llm import LLMResponse, LLMUsage

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "shop"
FIXTURE_EVAL = Path(__file__).parent / "fixtures" / "shop-eval.yaml"
JAVA_ROOT = "src/main/java/com/example/shop"
WEBHOOK_SECRET = "s3cret"


class FakeLLM:
    name = "fake"

    def __init__(self, reply: str, stop_reason: str = "end_turn"):
        self.reply = reply
        self.stop_reason = stop_reason
        self.calls = 0
        self.prompts: list[str] = []
        self.thinking: list[str | None] = []

    def complete(self, system, prompt, max_tokens, thinking=None):
        self.calls += 1
        self.prompts.append(prompt)
        self.thinking.append(thinking)
        return LLMResponse(self.reply, LLMUsage(100, 20), self.stop_reason)

    def stream(self, system, prompt, max_tokens):
        self.calls += 1
        self.prompts.append(prompt)
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else word + " "
        yield LLMUsage(100, 20)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        index_dir=tmp_path / "index",
        qdrant_url=":memory:",
        embedding_provider="hashing",
        reranker_model="",
        llm_provider="none",
        min_vector_similarity=-1.0,
        webhook_secret=WEBHOOK_SECRET,
    )


@pytest.fixture
def repo(tmp_path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, dst)
    return dst


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM("The retry logic lives in PaymentService.charge [1]. It backs off exponentially [1][99].")


@pytest.fixture
def atlas(settings, repo, fake_llm) -> CodeAtlas:
    instance = CodeAtlas(settings, embedder=HashingEmbedder(), llm=fake_llm)
    instance.index(repo)
    return instance
