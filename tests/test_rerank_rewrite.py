"""Cross-encoder reranker and LLM query rewriting, with fakes in place of the model and the LLM."""

import sys

import pytest

from codeatlas.llm import LLMResponse, LLMUsage
from codeatlas.models import Chunk
from codeatlas.retrieval.reranker import CrossEncoderReranker, sigmoid
from codeatlas.retrieval.rewrite import QueryRewriter


def chunk(text: str) -> Chunk:
    return Chunk(id=text, path="a/A.java", kind="method", text=text, embed_text=text, start_line=1, end_line=2)


def test_sigmoid_is_a_stable_probability():
    assert sigmoid(0) == 0.5
    assert sigmoid(-1000) == 0.0 and sigmoid(1000) == 1.0
    assert 0.0 < sigmoid(-5) < 0.5 < sigmoid(5) < 1.0


class FakeCrossEncoder:
    def __init__(self, old_api=False):
        self.old_api, self.pairs = old_api, None

    def predict(self, pairs, **kwargs):
        if self.old_api and "activation_fn" in kwargs:
            raise TypeError("unexpected keyword argument 'activation_fn'")
        assert ("activation_fct" if self.old_api else "activation_fn") in kwargs  # raw logits requested
        self.pairs = pairs
        return [float(len(text)) for _, text in pairs]


@pytest.mark.parametrize("old_api", [False, True])
def test_reranker_scores_query_chunk_pairs_as_raw_logits(old_api):
    pytest.importorskip("torch")
    reranker = CrossEncoderReranker("fake-model", max_chars=4)
    reranker._model = FakeCrossEncoder(old_api)
    assert reranker.score("q", [chunk("abcdefgh"), chunk("xy")]) == [4.0, 2.0]  # text cut to max_chars
    assert reranker._model.pairs == [("q", "abcd"), ("q", "xy")]


def test_reranker_needs_no_model_for_no_chunks():
    assert CrossEncoderReranker("never-loaded").score("q", []) == []


def test_reranker_explains_a_missing_local_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import fails
    with pytest.raises(ImportError, match=r"codeatlas\[local\]"):
        CrossEncoderReranker("any")._load()


class FakeLLM:
    def __init__(self, text):
        self.text, self.prompts = text, []

    def complete(self, system, prompt, max_tokens, thinking=None):
        self.prompts.append(prompt)
        return LLMResponse(self.text, LLMUsage(10, 10), "end_turn")


def test_rewriter_cleans_dedupes_and_caps_queries():
    llm = FakeLLM('1. OwnerController processFindForm\n- "findByLastNameStartingWith"\n\n'
                  "* OwnerController processFindForm\nhow does owner search work?\n2) OwnerRepository Page\nextra one")
    queries = QueryRewriter(lambda: llm, max_queries=3).rewrite("How does owner search work?")
    assert queries == ["OwnerController processFindForm", "findByLastNameStartingWith", "OwnerRepository Page"]
    assert "How does owner search work?" in llm.prompts[0]


def test_rewriter_returns_nothing_for_an_empty_reply():
    assert QueryRewriter(lambda: FakeLLM("")).rewrite("anything") == []
