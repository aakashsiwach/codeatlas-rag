"""Judge-run helpers, offline: the fake LLM answers and returns a fixed verdict, priced at a made-up rate."""

import json

import pytest
from conftest import FIXTURE_EVAL
from typer.testing import CliRunner

from codeatlas.cli import app

PRICES = '{"fake:offline": {"input_per_mtok": 1000, "output_per_mtok": 1000}}'  # inflated, so budgets bite


@pytest.fixture
def cli(tmp_path, repo, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "EMBEDDING_PROVIDER": "hashing",
        "QDRANT_URL": ":memory:",
        "RERANKER_MODEL": "",
        "LLM_PROVIDER": "fake",
        "LLM_MODEL": "offline",
        "LLM_PRICES": PRICES,
        "MIN_VECTOR_SIMILARITY": "0",
        "SETUPS": '{"quality": {"mode": "bm25+graph", "k": 3}}',
    }.items():
        monkeypatch.setenv(f"CODEATLAS_{name}", value)
    index_dir = str(tmp_path / "index")
    runner = CliRunner()
    assert runner.invoke(app, ["--index-dir", index_dir, "index", str(repo)]).exit_code == 0
    return lambda *args: runner.invoke(app, ["--index-dir", index_dir, *args])


def with_unreviewed(tmp_path):
    """The fixture set with one drafted, unreviewed reference answer."""
    text = FIXTURE_EVAL.read_text(encoding="utf-8").replace(
        "  - id: payment-retry\n",
        "  - id: payment-retry\n    reference_answer: Retries happen in charge.\n    reviewed: false\n",
        1,
    )
    path = tmp_path / "unreviewed.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_check_reviewed(cli, tmp_path):
    assert cli("check-reviewed", str(FIXTURE_EVAL)).exit_code == 0
    refused = cli("check-reviewed", str(with_unreviewed(tmp_path)))
    assert refused.exit_code == 2 and "payment-retry" in refused.output


def test_judge_check_reports_both_calls_and_the_estimate(cli, tmp_path):
    result = cli("judge-check", str(FIXTURE_EVAL), "--setup", "quality", "--passes", "2", "--budget", "1000")
    assert result.exit_code == 0, result.output
    assert "setup quality (bm25+graph, k=3)" in result.output
    assert "answer stop_reason=end_turn" in result.output and "truncated=False" in result.output
    assert "judge  stop_reason=end_turn" in result.output and "judge_error=None" in result.output
    assert "estimate: 2 pass(es) x (4 answers + 3 verdicts)" in result.output

    assert cli("judge-check", str(FIXTURE_EVAL), "--budget", "0.0001").exit_code == 4
    assert cli("judge-check", str(with_unreviewed(tmp_path))).exit_code == 2


def test_judge_check_refuses_unpriced_models_and_held_out_sets(cli, tmp_path, monkeypatch):
    held_out = tmp_path / "shop-test.yaml"
    held_out.write_text(FIXTURE_EVAL.read_text(encoding="utf-8"), encoding="utf-8")
    assert cli("judge-check", str(held_out)).exit_code == 2
    monkeypatch.setenv("CODEATLAS_LLM_PRICES", "{}")
    unpriced = cli("judge-check", str(FIXTURE_EVAL))
    assert unpriced.exit_code == 3 and "CODEATLAS_LLM_PRICES" in unpriced.output


def test_judge_summary_over_judged_runs(cli, tmp_path):
    paths = []
    for run in (1, 2):
        out = tmp_path / f"run{run}"
        result = cli("eval", str(FIXTURE_EVAL), "--setup", "quality", "--judge", "--out", str(out))
        assert result.exit_code == 0, result.output
        paths += [str(p) for p in out.glob("eval-*.json")]
    assert json.loads(open(paths[0], encoding="utf-8").read())["reports"][0]["summary"]["faithfulness"] == 1.0

    summary = cli("judge-summary", *paths)
    assert summary.exit_code == 0, summary.output
    assert "| bm25+graph | 2 | 1.000 ± 0.000 |" in summary.output
    assert "Total LLM cost of these runs: $" in summary.output
    assert cli("judge-summary", *paths, "--budget", "0.0001").exit_code == 4

    retrieval_only = tmp_path / "plain"
    cli("eval", str(FIXTURE_EVAL), "--setup", "quality", "--out", str(retrieval_only))
    plain = cli("judge-summary", *[str(p) for p in retrieval_only.glob("eval-*.json")])
    assert plain.exit_code == 2 and "--judge" in plain.output
