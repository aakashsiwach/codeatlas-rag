from pathlib import Path

from conftest import FIXTURE_EVAL
from typer.testing import CliRunner

from codeatlas.cli import app
from codeatlas.eval.dataset import ExpectedSource, load_eval_set
from codeatlas.eval.metrics import citation_precision, hit_at_k, recall_at_k, reciprocal_rank
from codeatlas.eval.runner import evaluate, to_markdown
from codeatlas.models import Chunk
from codeatlas.retrieval.retriever import RetrievalConfig


def chunk(path: str, symbol: str | None = None, text: str = "", kind: str = "method") -> Chunk:
    return Chunk(id=path + str(symbol), path=path, kind=kind, text=text, embed_text=text, start_line=1, end_line=2, symbol=symbol)


def test_expected_source_matching():
    c = chunk("src/main/java/x/owner/OwnerController.java", "x.owner.OwnerController#processFindForm")
    assert ExpectedSource.parse("owner/OwnerController.java").matches(c)
    assert ExpectedSource.parse("owner/OwnerController.java::OwnerController.processFindForm").matches(c)
    assert ExpectedSource.parse("owner/OwnerController.java::OwnerController").matches(c)
    assert not ExpectedSource.parse("owner/OwnerController.java::OwnerController.showOwner").matches(c)
    assert not ExpectedSource.parse("Controller.java").matches(c)

    naive = chunk("src/main/java/x/owner/OwnerController.java", None, "public String processFindForm(", "naive")
    assert ExpectedSource.parse("owner/OwnerController.java::OwnerController.processFindForm").matches(naive)


def test_retrieval_metrics():
    expected = [ExpectedSource.parse("a/A.java"), ExpectedSource.parse("b/B.java")]
    retrieved = [chunk("x/X.java"), chunk("src/a/A.java"), chunk("src/c/C.java")]
    assert recall_at_k(expected, retrieved, 3) == 0.5
    assert hit_at_k(expected, retrieved, 1) == 0.0
    assert reciprocal_rank(expected, retrieved) == 0.5
    assert citation_precision(expected, retrieved) == 1 / 3
    assert citation_precision(expected, []) is None


def test_petclinic_eval_set_is_well_formed():
    items = load_eval_set(Path(__file__).parents[1] / "eval" / "petclinic.yaml")
    assert len(items) >= 50
    assert len({i.id for i in items}) == len(items)
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

    with_answers = evaluate(atlas, items, RetrievalConfig.parse("hybrid", k=5), with_answers=True)
    assert "refusal_accuracy" in with_answers.summary()

    table = to_markdown([hybrid, graph])
    assert "| hybrid |" in table and "| hybrid+graph |" in table


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
