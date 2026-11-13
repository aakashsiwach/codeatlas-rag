import pytest
from conftest import FIXTURE_EVAL, FakeLLM

from codeatlas.atlas import CodeAtlas
from codeatlas.embeddings import HashingEmbedder
from codeatlas.eval.dataset import load_eval_set
from codeatlas.eval.runner import evaluate, report_markdown, run_metadata, total_cost
from codeatlas.llm import llm_cost_usd
from codeatlas.retrieval.retriever import RetrievalConfig

PRICES = {
    "bedrock:model-x": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
    "anthropic:model-x": {"input_per_mtok": 1.0, "output_per_mtok": 5.0},
}


def test_prices_are_per_provider_and_model(settings):
    s = settings.model_copy(update={"llm_provider": "bedrock", "llm_model": "model-x", "llm_prices": PRICES})
    assert llm_cost_usd(s, 1_000_000, 100_000) == 3.0
    assert llm_cost_usd(s.model_copy(update={"llm_provider": "anthropic"}), 1_000_000, 100_000) == 1.5
    assert llm_cost_usd(s.model_copy(update={"llm_model": "model-y"}), 10, 10) is None  # unpriced, not $0
    with pytest.raises(ValueError, match="input_per_mtok"):
        llm_cost_usd(s.model_copy(update={"llm_prices": {"bedrock:model-x": {"input": 1}}}), 1, 1)


def _atlas(settings, repo, prices):
    s = settings.model_copy(update={"llm_provider": "bedrock", "llm_model": "model-x", "llm_prices": prices})
    atlas = CodeAtlas(s, embedder=HashingEmbedder(), llm=FakeLLM("PaymentService.charge retries [1]."))
    atlas.index(repo)
    return atlas


def test_eval_sums_answer_and_judge_cost(settings, repo):
    atlas = _atlas(settings, repo, PRICES)
    answer = atlas.ask("Where is the payment retry logic?", mode="hybrid", k=5)
    assert answer.usage["cost_usd"] == pytest.approx((100 * 2.0 + 20 * 10.0) / 1e6)

    judge = FakeLLM('{"claims": 1, "supported": 1, "correct": null}')
    items = load_eval_set(FIXTURE_EVAL)
    report = evaluate(atlas, items, RetrievalConfig.parse("hybrid", k=5), judge=judge)
    summary = report.summary()
    judged = sum(1 for r in report.items if r.faithfulness is not None)
    calls = len(items) + judged  # every question answered (the fake never refuses) + one judge call each
    assert summary["llm_tokens_in"] == 100 * calls and summary["llm_tokens_out"] == 20 * calls
    assert summary["llm_cost_usd"] == pytest.approx(calls * (100 * 2.0 + 20 * 10.0) / 1e6, abs=1e-4)
    md = report_markdown([report], run_metadata(atlas, FIXTURE_EVAL, items, judge=True))
    assert "## LLM cost (answers + judge)" in md and "**total**" in md


def test_unpriced_models_are_reported_as_unpriced(settings, repo):
    atlas = _atlas(settings, repo, {})
    assert atlas.ask("Where is the payment retry logic?", mode="hybrid", k=5).usage["cost_usd"] is None
    report = evaluate(atlas, load_eval_set(FIXTURE_EVAL), RetrievalConfig.parse("hybrid", k=5), with_answers=True)
    assert report.summary()["llm_cost_usd"] == "unpriced"
    assert total_cost([report])["llm_cost_usd"] == "unpriced"
    retrieval_only = evaluate(atlas, load_eval_set(FIXTURE_EVAL), RetrievalConfig.parse("hybrid", k=5))
    assert "llm_cost_usd" not in retrieval_only.summary() and total_cost([retrieval_only]) == {}
