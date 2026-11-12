import json

import pytest
from conftest import FakeLLM

from codeatlas.answer import REFUSAL, count_uncited_sentences, extract_citations
from codeatlas.atlas import CodeAtlas
from codeatlas.embeddings import HashingEmbedder
from codeatlas.models import Chunk, ScoredChunk
from codeatlas.retrieval.retriever import TEST_INTENT, RetrievalConfig, _outlines_after_methods, _tests_last


def test_retrieval_config_parsing():
    cfg = RetrievalConfig.parse("hybrid+rerank+graph", k=5)
    assert (cfg.mode, cfg.rerank, cfg.graph, cfg.rewrite, cfg.k) == ("hybrid", True, True, False, 5)
    assert cfg.name == "hybrid+rerank+graph"
    assert RetrievalConfig.parse("vector").name == "vector"
    assert RetrievalConfig.parse("graph").name == "hybrid+graph"
    with pytest.raises(ValueError):
        RetrievalConfig.parse("hybrid+magic")


def test_ranking_options_are_typed_and_named():
    flipped = not RetrievalConfig().demote_tests
    opts = RetrievalConfig.parse_options([f"demote_tests={'on' if flipped else 'off'}", "rerank_pool=20"])
    assert opts == {"demote_tests": flipped, "rerank_pool": 20}
    cfg = RetrievalConfig.parse("hybrid+rerank", **opts)
    assert cfg.name == f"hybrid+rerank[rerank_pool=20,demote_tests={flipped}]"
    for bad in (["k=3"], ["demote_tests"], ["demote_tests=maybe"]):
        with pytest.raises(ValueError):
            RetrievalConfig.parse_options(bad)


def test_test_code_is_ranked_last_unless_the_query_is_about_tests():
    def sc(path: str) -> ScoredChunk:
        return ScoredChunk(Chunk(id=path, path=path, kind="method", text="", embed_text="", start_line=1, end_line=2), 1.0, {})

    ranked = [sc("src/test/java/a/OwnerTests.java"), sc("src/main/java/a/Owner.java"), sc("src/main/java/a/PetIT.java")]
    ranked.append(sc("src/main/java/a/Pet.java"))
    assert [s.chunk.path.rsplit("/", 1)[1] for s in _tests_last(ranked)] == [
        "Owner.java", "Pet.java", "OwnerTests.java", "PetIT.java"
    ]
    assert TEST_INTENT.search("Which tests cover the duplicate pet check?")


def test_class_outline_ranks_below_its_own_methods():
    def sc(symbol: str, kind: str) -> ScoredChunk:
        return ScoredChunk(Chunk(id=symbol, path="A.java", kind=kind, text="", embed_text="", start_line=1, end_line=2, symbol=symbol), 1.0, {})

    ranked = [sc("x.Owner", "class"), sc("x.Pet", "class"), sc("x.Owner#addPet", "method"), sc("x.Vet#name", "method")]
    ranked += [sc("x.Owner#getPet", "method"), sc("x.OwnerController#show", "method")]
    assert [s.chunk.symbol for s in _outlines_after_methods(ranked)] == [
        "x.Pet",  # no Pet method retrieved: stays where it was
        "x.Owner#addPet",
        "x.Vet#name",
        "x.Owner#getPet",
        "x.Owner",  # after its lowest-ranked method; x.OwnerController is a different class
        "x.OwnerController#show",
    ]
    assert TEST_INTENT.search("Is there a JUnit or Mockito setup?")
    assert not TEST_INTENT.search("What happens when a duplicate pet is added? Show the latest owner.")


@pytest.mark.parametrize("mode", ["vector", "bm25", "hybrid"])
def test_each_mode_finds_the_retry_logic(atlas, mode):
    rr = atlas.retrieve("Where is the payment retry logic?", mode=mode, k=5)
    assert any(
        "PaymentService" in (sc.chunk.symbol or "") or "payment-retries" in sc.chunk.path for sc in rr.chunks
    )
    assert rr.confidence_source == ("bm25" if mode == "bm25" else "vector")


def test_graph_mode_answers_impact_questions(atlas):
    rr = atlas.retrieve("What breaks if I change the Customer entity?", mode="hybrid+graph", k=8)
    assert rr.confidence_source == "graph"
    assert "PaymentService#charge" in rr.graph_report
    assert rr.chunks[0].chunk.symbol == "com.example.shop.customer.Customer"
    assert len(rr.chunks) <= 8


def test_graph_mode_finds_endpoint_callers(atlas):
    rr = atlas.retrieve("Which services call the /orders/{id} endpoint?", mode="hybrid+graph", k=8)
    report = rr.graph_report
    assert "served by: OrderController#getOrder" in report
    assert "OrderLookupClient#fetchOrder" in report
    assert "ReportingService#describeOrder" in report
    assert "InvoiceJob#syncInvoices" in report


def test_answer_validates_citations_and_traces(atlas, fake_llm, settings):
    answer = atlas.ask("Where is the payment retry logic?", mode="hybrid", k=5)
    assert not answer.refused
    assert [c.index for c in answer.citations] == [1]
    assert answer.invalid_citations == [99]
    assert answer.usage["input_tokens"] == 100
    assert fake_llm.calls == 1
    assert "[1] " in fake_llm.prompts[0]

    traces = (settings.index_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(traces[-1])
    assert record["trace_id"] == answer.trace_id
    assert [s["name"] for s in record["spans"]] == ["retrieve", "generate"]


def test_low_confidence_refuses_without_calling_the_llm(settings, repo):
    llm = FakeLLM("should not be called")
    strict = settings.model_copy(update={"min_vector_similarity": 1.01})
    atlas = CodeAtlas(strict, embedder=HashingEmbedder(), llm=llm)
    atlas.index(repo)
    answer = atlas.ask("How are Kafka consumer offsets committed?", mode="hybrid")
    assert answer.refused
    assert answer.answer.startswith(REFUSAL)
    assert answer.citations == []
    assert llm.calls == 0


def test_streaming_emits_sources_tokens_and_done(atlas):
    events = list(atlas.ask_stream("Where is the payment retry logic?", mode="hybrid", k=5))
    assert events[0]["event"] == "sources"
    assert any(e["event"] == "token" for e in events)
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["citations"][0]["index"] == 1


def test_citation_helpers():
    assert extract_citations("A [1]. B [2][3]. C [7].", 3) == ([1, 2, 3], [7])
    text = "PaymentService.charge retries on timeouts [1].\nThis sentence makes a claim with no citation at all."
    assert count_uncited_sentences(text) == 1
    assert count_uncited_sentences("```java\nno citations in code blocks are fine\n```") == 0
