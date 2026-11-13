import json
from pathlib import Path

import pytest
from conftest import FIXTURE_EVAL, FIXTURE_REPO, JAVA_ROOT, FakeLLM
from typer.testing import CliRunner

from codeatlas.cli import app
from codeatlas.eval.dataset import EvalItem, ExpectedSource, load_eval_set, resolve_expected_sources
from codeatlas.eval.judge import judge_answer
from codeatlas.eval.metrics import citation_precision, hit_at_k, recall_at_k, reciprocal_rank
from codeatlas.eval.runner import category_markdown, evaluate, report_markdown, run_metadata, to_markdown
from codeatlas.models import Chunk
from codeatlas.retrieval.retriever import RetrievalConfig


def chunk(path: str, symbol: str | None = None, text: str = "", kind: str = "method") -> Chunk:
    return Chunk(id=path + str(symbol), path=path, kind=kind, text=text, embed_text=text, start_line=1, end_line=2, symbol=symbol)


def span(path: str, start: int, end: int, kind: str = "method", symbol: str | None = None, text: str = "") -> Chunk:
    return Chunk(id=f"{path}:{start}", path=path, kind=kind, text=text, embed_text=text, start_line=start, end_line=end, symbol=symbol)


PAYMENT = f"{JAVA_ROOT}/payment/PaymentService.java"  # class: 9-34; charge(): Javadoc 20-23, declaration 24-28


def resolved(*specs: str) -> list[ExpectedSource]:
    item = EvalItem(id="q", question="q", expected_sources=[ExpectedSource.parse(s) for s in specs])
    return resolve_expected_sources([item], FIXTURE_REPO)[0].expected_sources


def test_expected_source_matching_is_by_definition_lines():
    method, cls, whole_file = resolved(
        "payment/PaymentService.java::PaymentService.charge",
        "payment/PaymentService.java::PaymentService",
        "payment/PaymentService.java",
    )
    assert method.definitions == ((PAYMENT, 24, 28),) and method.is_method  # Javadoc excluded
    assert cls.definitions == ((PAYMENT, 9, 34),) and not cls.is_method

    # Same rule for every chunker: overlap the definition by >= min(5 lines, 50%); for charge() that is 3 lines.
    assert method.matches(span(PAYMENT, 20, 28, symbol="com.example.shop.payment.PaymentService#charge"))
    assert method.matches(span(PAYMENT, 1, 26, kind="naive"))  # 3 lines of the declaration
    assert not method.matches(span(PAYMENT, 1, 25, kind="naive"))  # only 2
    assert not method.matches(span(PAYMENT, 1, 23, kind="naive"))  # only the Javadoc
    assert method.matches(span(PAYMENT, 26, 40, kind="naive"))
    assert not method.matches(span(PAYMENT, 27, 40, kind="naive"))  # body tail and closing brace only
    # A naive chunk that only mentions the name (e.g. a call site or a later method) does not count.
    assert not method.matches(span(PAYMENT, 29, 34, kind="naive", text="return charge(order);"))
    # A class outline spans the class but holds only signatures: it covers the class, not its methods.
    outline = span(PAYMENT, 9, 34, kind="class", symbol="com.example.shop.payment.PaymentService")
    assert not method.matches(outline)
    assert cls.matches(outline) and cls.matches(span(PAYMENT, 24, 28))  # 5 lines of a 26-line class
    assert not cls.matches(span(PAYMENT, 30, 33))  # 4 lines: below min(5, 13)
    # Path must match too, and file-level sources match any chunk of the file.
    assert not method.matches(span(f"{JAVA_ROOT}/order/Order.java", 20, 28))
    assert whole_file.matches(span(PAYMENT, 1, 5, kind="naive"))
    assert not ExpectedSource.parse("Service.java").matches(span(PAYMENT, 1, 5))


def test_unresolvable_expected_sources_fail_loudly():
    with pytest.raises(ValueError, match="PaymentService.refund"):
        resolved("payment/PaymentService.java::PaymentService.refund")
    with pytest.raises(ValueError, match="unresolved"):
        ExpectedSource.parse("payment/PaymentService.java::PaymentService.charge").matches(span(PAYMENT, 20, 28))


def test_judge_forwards_thinking_and_never_scores_a_bad_verdict_as_faithful():
    ctx = [span(PAYMENT, 20, 28, text="charge() retries three times")]
    good = FakeLLM('{"claims": 4, "supported": 3, "correct": true}')
    verdict = judge_answer(good, "q", "a", ctx, thinking="disabled")
    assert good.thinking == ["disabled"]
    assert (verdict["faithfulness"], verdict["correct"], verdict["judge_error"]) == (0.75, True, None)

    truncated = judge_answer(FakeLLM('{"claims": 4, "supp', stop_reason="max_tokens"), "q", "a", ctx)
    assert truncated["faithfulness"] is None and truncated["judge_error"] == "judge_truncated"
    garbled = judge_answer(FakeLLM("I think it is mostly right."), "q", "a", ctx)
    assert garbled["faithfulness"] is None and garbled["judge_error"] == "judge_unparseable"


def test_bedrock_request_carries_the_thinking_type():
    pytest.importorskip("boto3")
    from codeatlas.llm import BedrockLLM

    llm = BedrockLLM("some-model", region="us-east-1")  # building a request needs no credentials
    assert llm._request("s", "p", 300, "disabled")["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
    assert "additionalModelRequestFields" not in llm._request("s", "p", 300)


def test_retrieval_metrics():
    expected = [ExpectedSource.parse("a/A.java"), ExpectedSource.parse("b/B.java")]
    retrieved = [chunk("x/X.java"), chunk("src/a/A.java"), chunk("src/c/C.java")]
    assert recall_at_k(expected, retrieved, 3) == 0.5
    assert hit_at_k(expected, retrieved, 1) == 0.0
    assert reciprocal_rank(expected, retrieved) == 0.5
    assert citation_precision(expected, retrieved) == 1 / 3
    assert citation_precision(expected, []) is None


def test_petclinic_eval_splits_are_well_formed():
    eval_dir = Path(__file__).parents[1] / "eval"
    dev = load_eval_set(eval_dir / "petclinic-dev.yaml")
    test = load_eval_set(eval_dir / "petclinic-test.yaml")
    items = dev + test
    assert len(items) >= 50 and len(dev) > len(test) > 0
    assert len({i.id for i in items}) == len(items)  # no question in both splits
    assert {i.category for i in dev} == {i.category for i in test}  # stratified: every category in both
    for item in items:
        assert bool(item.expected_sources) == item.answerable, item.id


def test_evaluate_on_fixture(atlas):
    items = load_eval_set(FIXTURE_EVAL)
    assert [i.answerable for i in items] == [True, True, True, False]

    hybrid = evaluate(atlas, items, RetrievalConfig.parse("hybrid", k=5))
    graph = evaluate(atlas, items, RetrievalConfig.parse("hybrid+graph", k=5))
    for summary in (hybrid.summary(), graph.summary()):
        assert summary["questions"] == 4
        assert 0.0 <= summary["recall@5"] <= 1.0
        assert 0.0 <= summary["mrr"] <= 1.0
    impact_item = next(r for r in graph.items if r.id == "customer-impact")
    assert impact_item.hit == 1.0

    for spec in ("hybrid", "hybrid+graph"):  # a deeper cutoff adds a column but never changes top-k
        deep = evaluate(atlas, items, RetrievalConfig.parse(spec, k=2), deep_k=5).summary()
        shallow = evaluate(atlas, items, RetrievalConfig.parse(spec, k=2)).summary()
        assert deep["recall@5"] >= deep["recall@2"] == shallow["recall@2"]
        assert deep["mrr"] == shallow["mrr"] and "recall@5" not in shallow

    with_answers = evaluate(atlas, items, RetrievalConfig.parse("hybrid", k=5), with_answers=True)
    assert "refusal_accuracy" in with_answers.summary()

    table = to_markdown([hybrid, graph])
    assert "| hybrid |" in table and "| hybrid+graph |" in table

    categories = graph.by_category()
    assert set(categories) == {"locate", "endpoint", "impact"}  # unanswerable questions are excluded
    assert categories["impact"]["questions"] == 1
    weakest = graph.weakest(2)
    assert len(weakest) == 2 and weakest[0].recall <= weakest[1].recall
    assert weakest[0].expected

    metadata = run_metadata(atlas, FIXTURE_EVAL, items)
    assert metadata["questions"] == 4 and metadata["answerable"] == 3
    assert metadata["embedder"] == "hashing-512" and metadata["chunker"] == "ast"
    assert metadata["index_build"]["files"] > 0
    assert "llm" not in metadata
    report = report_markdown([hybrid, graph], metadata, weakest=2)
    for heading in ("## Run", "## Overall", "## Recall@5 by category", "### hybrid+graph"):
        assert heading in report
    assert "| impact (1) |" in category_markdown([hybrid, graph])


def test_cli_index_impact_and_search(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEATLAS_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEATLAS_QDRANT_URL", ":memory:")
    monkeypatch.setenv("CODEATLAS_RERANKER_MODEL", "")
    runner = CliRunner()
    index_dir = str(tmp_path / "cli-index")

    result = runner.invoke(app, ["--index-dir", index_dir, "index", str(repo)])
    assert result.exit_code == 0, result.output
    assert "changed" in result.output

    result = runner.invoke(app, ["--index-dir", index_dir, "impact", "Customer"])
    assert result.exit_code == 0, result.output
    assert "Direct dependents" in result.output

    result = runner.invoke(app, ["--index-dir", index_dir, "ask", "PaymentService charge", "--mode", "bm25", "--sources-only"])
    assert result.exit_code == 0, result.output
    assert "PaymentService" in result.output

    out = tmp_path / "results"
    result = runner.invoke(
        app,
        ["--index-dir", index_dir, "eval", str(FIXTURE_EVAL), "--configs", "bm25,bm25+graph", "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "| bm25+graph |" in result.output
    reports = list(out.glob("eval-*.md"))
    assert len(reports) == 1
    assert "## Recall@5 by category" in reports[0].read_text(encoding="utf-8")
    data = json.loads(next(out.glob("eval-*.json")).read_text(encoding="utf-8"))
    assert data["metadata"]["questions"] == 4
    assert [r["summary"]["config"] for r in data["reports"]] == ["bm25", "bm25+graph"]


def test_bedrock_stream_reports_the_stop_reason():
    pytest.importorskip("boto3")
    from codeatlas.llm import BedrockLLM, LLMUsage

    class Stub:
        def __init__(self):
            self.request = None

        def converse_stream(self, **request):
            self.request = request
            return {
                "stream": [
                    {"contentBlockDelta": {"delta": {"text": "Partial ans"}}},
                    {"messageStop": {"stopReason": "max_tokens"}},
                    {"metadata": {"usage": {"inputTokens": 7, "outputTokens": 3}}},
                ]
            }

    llm = BedrockLLM("some-model", region="us-east-1")
    llm._client = Stub()
    items = list(llm.stream("s", "p", 10, thinking="disabled"))
    assert items[0] == "Partial ans"
    assert items[-1] == LLMUsage(7, 3, "max_tokens")
    assert llm._client.request["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}


def test_eval_counts_truncated_verdicts_and_uses_per_model_judge_thinking(atlas):
    atlas.settings.llm_model = "judge-model"
    atlas.settings.thinking_by_model = {"judge-model": {"judge": "between_tools"}}
    items = load_eval_set(FIXTURE_EVAL)
    judge = FakeLLM('{"claims": 3, "supp', stop_reason="max_tokens")
    report = evaluate(atlas, items, RetrievalConfig.parse("hybrid", k=5), judge=judge)
    summary = report.summary()
    judged = [r for r in report.items if r.answerable and not r.refused]
    assert summary["judge_truncated"] == len(judged) > 0 and summary["judge_unparseable"] == 0
    assert summary["faithfulness"] is None  # truncated verdicts are not scores
    assert set(judge.thinking) == {"between_tools"}
    meta = run_metadata(atlas, FIXTURE_EVAL, items, with_answers=True, judge=True)
    assert meta["judge_thinking"] == "between_tools"


def test_model_loading_is_excluded_from_latency(atlas):
    import time

    class SlowFirstReranker:
        def __init__(self):
            self.loaded = False

        def score(self, query, chunks):
            if not self.loaded:
                time.sleep(0.4)  # simulated model load on first use
                self.loaded = True
            return [0.0] * len(chunks)

    atlas.retriever.reranker = SlowFirstReranker()
    report = evaluate(atlas, load_eval_set(FIXTURE_EVAL), RetrievalConfig.parse("hybrid+rerank", k=5))
    assert report.warmup_ms >= 400
    assert max(r.latency_ms for r in report.items) < 400
    assert report.to_dict()["warmup_ms"] == report.warmup_ms


def test_reports_record_relative_paths_remote_without_credentials_and_the_llm_that_ran(atlas, repo, monkeypatch):
    import subprocess

    from codeatlas.eval.runner import display_path, git_remote

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://someone:s3cr3t@github.com/acme/shop.git"],
        check=True,
    )
    assert git_remote(repo) == "https://github.com/acme/shop.git"
    monkeypatch.chdir(repo.parent)
    meta = run_metadata(atlas, FIXTURE_EVAL, load_eval_set(FIXTURE_EVAL), with_answers=True)
    assert meta["repo_path"] == "repo" and meta["repo_remote"] == "https://github.com/acme/shop.git"
    assert meta["llm"] == "fake"  # the LLM object that ran, not just the configured setting
    assert not any(str(v).startswith(("/", "\\")) or ":\\" in str(v) for v in meta.values() if isinstance(v, str))
    assert display_path(repo / "src", base=repo) == "src"
    assert display_path(Path("/elsewhere/x"), base=repo) == "x"


def test_eval_refuses_the_held_out_split_without_final(tmp_path, monkeypatch):
    from codeatlas.cli import is_held_out

    held_out = tmp_path / "petclinic-test.yaml"
    held_out.write_text(FIXTURE_EVAL.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["eval", str(held_out)])
    assert result.exit_code == 2 and "held-out test split" in result.output and "--final" in result.output
    assert is_held_out(Path("eval/petclinic-test.yaml")) and is_held_out(Path("x-TEST.yml"))
    assert not is_held_out(Path("eval/petclinic-dev.yaml")) and not is_held_out(Path("contest.yaml"))


def test_refusal_thresholds_are_per_model(atlas):
    from codeatlas.answer import confidence_threshold

    s = atlas.settings.model_copy(update={"min_vector_similarity": 0.3, "min_rerank_confidence": 0.05,
                                          "reranker_model": "rerank-a",
                                          "min_confidence_by_model": {"rerank-a": 0.4, "hashing-512": 0.6}})
    assert confidence_threshold(s, "rerank", "hashing-512") == 0.4
    assert confidence_threshold(s, "vector", "hashing-512") == 0.6
    assert confidence_threshold(s, "vector", "other-embedder") == 0.3
    assert confidence_threshold(s.model_copy(update={"reranker_model": "rerank-b"}), "rerank", "x") == 0.05
    assert confidence_threshold(s, "bm25", "x") is None


def test_calibration_picks_the_separating_threshold():
    from codeatlas.eval.runner import EvalReport, ItemResult, calibrate

    def item(i, answerable, conf):
        return ItemResult(id=str(i), question="q", category="c", answerable=answerable, latency_ms=1.0, retrieved=[],
                          confidence=conf, confidence_source="rerank", threshold=0.05, threshold_key="rerank-a")

    confs = [(True, 0.9), (True, 0.7), (True, 0.55), (True, 0.2), (False, 0.3), (False, 0.1), (False, 0.05)]
    report = EvalReport(config="hybrid+rerank", k=5, items=[item(i, a, c) for i, (a, c) in enumerate(confs)])
    cal = calibrate(report)
    # Best cut lies between 0.30 (highest unanswerable) and 0.55: all unanswerable refused, one false refusal.
    assert 0.3 < cal["best_threshold"] < 0.55
    assert cal["best"]["unanswerable_refused"] == "3/3" and cal["best"]["false_refusals"] == "1/4"
    assert cal["current_threshold"] == 0.05 and cal["current"]["unanswerable_refused"] == "0/3"
    assert cal["model"] == "rerank-a"
    assert calibrate(EvalReport(config="x", k=5, items=[item(0, True, 0.5)])) is None  # needs both classes


def test_eval_items_record_confidence_and_cli_calibrates(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEATLAS_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEATLAS_QDRANT_URL", ":memory:")
    monkeypatch.setenv("CODEATLAS_RERANKER_MODEL", "")
    index_dir = str(tmp_path / "cal-index")
    runner = CliRunner()
    assert runner.invoke(app, ["--index-dir", index_dir, "index", str(repo)]).exit_code == 0
    out = tmp_path / "results"
    result = runner.invoke(app, ["--index-dir", index_dir, "eval", str(FIXTURE_EVAL), "--configs", "hybrid",
                                 "--calibrate", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Refusal calibration" in result.output
    data = json.loads(next(out.glob("eval-*.json")).read_text(encoding="utf-8"))
    assert data["calibration"][0]["config"] == "hybrid" and "best_threshold" in data["calibration"][0]
    assert "vector (hashing-512)" in data["metadata"]["thresholds"]
    assert "## Refusal calibration" in next(out.glob("eval-*.md")).read_text(encoding="utf-8")


def test_eval_items_record_the_confidence_and_threshold_used(atlas):
    report = evaluate(atlas, load_eval_set(FIXTURE_EVAL), RetrievalConfig.parse("hybrid+graph", k=5))
    for r in report.items:
        assert r.confidence_source == "vector" and r.threshold_key == "hashing-512"  # graph never overrides it
        assert r.threshold == atlas.settings.min_vector_similarity and r.confidence is not None
