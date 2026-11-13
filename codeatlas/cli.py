"""Command-line interface: index, ask, impact, eval, serve, stats."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer

from codeatlas.config import Settings

app = typer.Typer(help="CodeAtlas: cited answers about Java/Spring Boot codebases.", no_args_is_help=True)


def _settings(ctx: typer.Context) -> Settings:
    return ctx.obj


def _atlas(ctx: typer.Context):
    from codeatlas.atlas import CodeAtlas
    from codeatlas.storage.vector_store import IndexLockedError

    try:
        return CodeAtlas(_settings(ctx))
    except IndexLockedError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(3) from exc


@app.callback()
def main(
    ctx: typer.Context,
    index_dir: Optional[Path] = typer.Option(None, "--index-dir", help="Index directory (default .codeatlas)"),
) -> None:
    settings = Settings()
    if index_dir is not None:
        settings = settings.model_copy(update={"index_dir": index_dir})
    ctx.obj = settings


@app.command()
def index(
    ctx: typer.Context,
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Repository root"),
    force: bool = typer.Option(False, "--force", help="Drop the index and rebuild from scratch"),
    chunker: str = typer.Option("ast", help="ast (tree-sitter) or naive (fixed-size baseline)"),
) -> None:
    """Index a repository. Re-runs only re-embed files whose content changed."""
    stats = _atlas(ctx).index(repo, force=force, chunker=chunker)
    typer.echo(
        f"{stats.mode} index of {stats.repo}: {stats.files_scanned} files scanned, "
        f"{stats.files_changed} changed, {stats.files_unchanged} unchanged, {stats.files_deleted} deleted, "
        f"{stats.chunks_written} chunks embedded (~{stats.embed_tokens} tokens, ${stats.embed_cost_usd}) "
        f"in {stats.duration_s}s"
    )
    for err in stats.errors:
        typer.secho(f"  error: {err}", fg=typer.colors.YELLOW)


@app.command()
def ask(
    ctx: typer.Context,
    question: str = typer.Argument(...),
    mode: Optional[str] = typer.Option(None, help="e.g. hybrid, hybrid+rerank+graph"),
    k: Optional[int] = typer.Option(None, "-k", help="Number of chunks to retrieve"),
    sources_only: bool = typer.Option(False, "--sources-only", help="Skip generation, show retrieval"),
) -> None:
    """Ask a question about the indexed codebase."""
    atlas = _atlas(ctx)
    if sources_only:
        rr = atlas.retrieve(question, mode, k)
        if rr.graph_report:
            typer.echo(rr.graph_report + "\n")
        for i, sc in enumerate(rr.chunks, start=1):
            typer.echo(f"[{i}] {sc.chunk.location} {sc.chunk.symbol or ''} score={sc.score:.4f} {sc.signals}")
        typer.echo(f"confidence={rr.confidence:.3f} ({rr.confidence_source})")
        return

    from codeatlas.llm import LLMNotConfiguredError

    try:
        answer = atlas.ask(question, mode, k)
    except LLMNotConfiguredError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.echo(answer.answer + "\n")
    for c in answer.citations:
        typer.echo(f"[{c.index}] {c.chunk.location} {c.chunk.symbol or ''}")
    if answer.invalid_citations:
        typer.secho(f"invalid citations: {answer.invalid_citations}", fg=typer.colors.YELLOW)
    typer.echo(
        f"\nconfidence={answer.confidence:.3f} ({answer.confidence_source}) "
        f"tokens={answer.usage.get('input_tokens', 0)}+{answer.usage.get('output_tokens', 0)} "
        f"cost={_money(answer.usage.get('cost_usd'))} trace={answer.trace_id}"
    )


@app.command()
def impact(
    ctx: typer.Context,
    symbol: str = typer.Argument(..., help="Class, Class.method or method name"),
    depth: int = typer.Option(3, help="How many dependency hops to follow"),
) -> None:
    """Show what depends on a class or method (change-impact analysis)."""
    targets, _, report = _atlas(ctx).impact(symbol, depth)
    if not targets:
        typer.secho(f"No class or method matching {symbol!r}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo(report)


@app.command("eval")
def evaluate_cmd(
    ctx: typer.Context,
    eval_set: Path = typer.Argument(..., exists=True, dir_okay=False),
    configs: str = typer.Option(
        "vector,bm25,hybrid,hybrid+rerank,hybrid+rerank+graph", help="Comma-separated retrieval configs"
    ),
    k: int = typer.Option(5, "-k"),
    deep_k: Optional[int] = typer.Option(None, "--deep-k", help="Also report recall at this larger cutoff, e.g. 10"),
    option: List[str] = typer.Option(
        [], "--option", "-o", help="Ranking option override for every config, e.g. -o demote_tests=false"
    ),
    answers: bool = typer.Option(False, "--answers", help="Also generate answers and score them"),
    judge: bool = typer.Option(False, "--judge", help="Score faithfulness with an LLM judge (implies --answers)"),
    out: Path = typer.Option(Path("results"), help="Directory for JSON and Markdown results"),
    weakest: int = typer.Option(5, help="Weakest questions to list per configuration"),
    calibrate: bool = typer.Option(
        False, "--calibrate", help="Report the refusal threshold that best separates (un)answerable questions"
    ),
    final: bool = typer.Option(
        False, "--final", help="Allow a held-out *-test.yaml set: only once the setup is frozen"
    ),
) -> None:
    """Compare retrieval configurations on an evaluation set."""
    if is_held_out(eval_set) and not final:
        typer.secho(
            f"{eval_set.name} is a held-out test split. Tune on the dev split; run this only once the setup is "
            "frozen, with --final.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)
    from codeatlas.eval.dataset import load_eval_set
    from codeatlas.eval.runner import calibrate as calibrate_report
    from codeatlas.eval.runner import (
        calibration_markdown,
        category_markdown,
        evaluate,
        report_markdown,
        run_metadata,
        to_markdown,
        total_cost,
    )
    from codeatlas.retrieval.retriever import RetrievalConfig

    atlas = _atlas(ctx)
    items = load_eval_set(eval_set)
    with_answers = answers or judge
    judge_llm = atlas.llm if judge else None
    reports = []
    try:
        overrides = RetrievalConfig.parse_options(option)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--option") from exc
    metadata = run_metadata(atlas, eval_set, items, with_answers=with_answers, judge=judge)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def write_reports() -> None:
        """Rewritten after every configuration, so a crash or Ctrl-C keeps the finished ones."""
        calibrations = [c for r in reports if (c := calibrate_report(r))] if calibrate else None
        (out / f"eval-{stamp}.md").write_text(
            report_markdown(reports, metadata, weakest, calibrations), encoding="utf-8"
        )
        (out / f"eval-{stamp}.json").write_text(
            json.dumps(
                {"metadata": metadata, "reports": [r.to_dict() for r in reports], "calibration": calibrations},
                indent=2,
            ),
            encoding="utf-8",
        )

    for spec in [c.strip() for c in configs.split(",") if c.strip()]:
        cfg = RetrievalConfig.parse(spec, k=k, **overrides)
        typer.echo(f"evaluating {cfg.name} on {len(items)} questions...")
        reports.append(evaluate(atlas, items, cfg, with_answers=with_answers, judge=judge_llm, deep_k=deep_k))
        write_reports()
        errors = reports[-1].summary().get("errors")
        if errors:
            typer.secho(f"  {errors} question(s) failed with LLM errors; see the report", fg=typer.colors.YELLOW)

    typer.echo("\n" + to_markdown(reports) + "\n\n" + category_markdown(reports))

    if calibrate:
        typer.echo("\nRefusal calibration:\n" + calibration_markdown([c for r in reports if (c := calibrate_report(r))]))

    cost = total_cost(reports)
    if cost:
        typer.echo(
            f"\nLLM usage (answers + judge): {cost['llm_tokens_in']} input + {cost['llm_tokens_out']} output tokens, "
            f"cost {_money(cost['llm_cost_usd']) if cost['llm_cost_usd'] != 'unpriced' else 'unpriced'}"
        )
    typer.echo(f"\nfull report (run metadata, weakest questions) written to {out}/eval-{stamp}.{{md,json}}")


@app.command()
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Run the HTTP API."""
    settings = _settings(ctx)
    if not _is_loopback(host) and not settings.api_token and not settings.allow_unauthenticated:
        typer.secho(
            f"Refusing to serve on {host} without authentication. Set CODEATLAS_API_TOKEN, bind to 127.0.0.1, "
            "or set CODEATLAS_ALLOW_UNAUTHENTICATED=true if the port is only reachable locally.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)

    import uvicorn

    from codeatlas.api.app import create_app

    uvicorn.run(create_app(settings), host=host, port=port)


def is_held_out(eval_set: Path) -> bool:
    """Held-out test splits are named *-test.yaml / *-test.yml."""
    return eval_set.stem.lower().endswith("-test") and eval_set.suffix.lower() in (".yaml", ".yml")


def _money(value: float | None) -> str:
    return "unpriced" if value is None else f"${value:.4f}"


def _is_loopback(host: str) -> bool:
    import ipaddress

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.command()
def doctor(
    ctx: typer.Context,
    llm: bool = typer.Option(
        False, "--llm", help="Also make ONE minimal LLM call (1 output token). The provider bills it."
    ),
    skip_models: bool = typer.Option(False, "--skip-models", help="Do not load the embedding and reranker models"),
) -> None:
    """Check Python, the index (and its lock), the models and the LLM provider configuration."""
    from codeatlas.doctor import FAIL, run_checks

    colors = {"ok": typer.colors.GREEN, "warn": typer.colors.YELLOW, "fail": typer.colors.RED}
    checks = run_checks(_settings(ctx), llm_call=llm, models=not skip_models)
    for c in checks:
        typer.secho(f"{c.status:<4}  {c.name:<16} {c.detail}", fg=colors[c.status])
    if any(c.status == FAIL for c in checks):
        raise typer.Exit(1)


@app.command()
def stats(ctx: typer.Context) -> None:
    """Show index size, graph size and recent index runs."""
    typer.echo(json.dumps(_atlas(ctx).stats(), indent=2, default=str))


@app.command()
def runs(ctx: typer.Context, limit: int = typer.Option(10)) -> None:
    """Show recent index runs with changed-file counts and embedding cost."""
    from codeatlas.storage.chunk_store import ChunkStore

    settings = _settings(ctx)
    store = ChunkStore(settings.index_dir / "codeatlas.db")
    rows: List[dict] = store.runs(limit)
    for r in rows:
        typer.echo(
            f"{r['started_at']} {r['mode']:<11} scanned={r['files_scanned']} changed={r['files_changed']} "
            f"deleted={r['files_deleted']} chunks={r['chunks_written']} tokens~{r['embed_tokens']} "
            f"cost=${r['embed_cost_usd']} {r['duration_s']}s"
        )


if __name__ == "__main__":
    app()
