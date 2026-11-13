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
    if stats.chunks_over_embed_limit:
        typer.secho(
            f"  note: {stats.chunks_over_embed_limit} chunks are longer than the embedding model reads; vector search "
            "sees only their start (BM25 and the reranker see more). A smaller CODEATLAS_MAX_CHUNK_LINES shortens them.",
            fg=typer.colors.YELLOW,
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


DEFAULT_EVAL_CONFIGS = "vector,bm25,hybrid,hybrid+rerank,hybrid+rerank+graph"


@app.command("eval")
def evaluate_cmd(
    ctx: typer.Context,
    eval_set: Path = typer.Argument(..., exists=True, dir_okay=False),
    configs: Optional[str] = typer.Option(
        None, help=f"Comma-separated retrieval configs [default: {DEFAULT_EVAL_CONFIGS}]"
    ),
    setup: Optional[str] = typer.Option(
        None, "--setup", help="Evaluate a named setup (e.g. fast, quality): its mode and k. Excludes --configs"
    ),
    k: Optional[int] = typer.Option(None, "-k", help="Chunks per question [default: 5, or the setup's k]"),
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
    if setup is not None:
        if configs is not None:
            raise typer.BadParameter("pass either --setup or --configs", param_hint="--setup")
        try:
            setup_cfg = atlas.setup_config(setup)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--setup") from exc
        specs, k = [setup_cfg.name], k or setup_cfg.k
    else:
        specs, k = [c.strip() for c in (configs or DEFAULT_EVAL_CONFIGS).split(",") if c.strip()], k or 5
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

    for spec in specs:
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


def _require_final(eval_set: Path, final: bool) -> None:
    if is_held_out(eval_set) and not final:
        typer.secho(
            f"{eval_set.name} is a held-out test split; pass --final once the setup is frozen.", fg=typer.colors.RED
        )
        raise typer.Exit(2)


def _require_reviewed(items) -> None:
    from codeatlas.eval.judge_run import unreviewed

    pending = unreviewed(items)
    if pending:
        typer.secho(
            f"{len(pending)} of {len(items)} reference answers are not reviewed (reviewed: false): "
            + ", ".join(pending)
            + ". Review them and set reviewed: true before a judged run.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)


@app.command("check-reviewed")
def check_reviewed(eval_set: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Exit 2 unless every reference answer in the set is marked reviewed. Makes no calls."""
    from codeatlas.eval.dataset import load_eval_set

    items = load_eval_set(eval_set)
    _require_reviewed(items)
    with_reference = sum(1 for i in items if i.reference_answer)
    typer.echo(f"{eval_set.name}: all {with_reference} reference answers are reviewed")


@app.command("judge-check")
def judge_check(
    ctx: typer.Context,
    eval_set: Path = typer.Argument(..., exists=True, dir_okay=False),
    setup: str = typer.Option("quality", "--setup", help="Named setup to answer with"),
    passes: int = typer.Option(3, "--passes", min=1, help="Judged runs the estimate covers"),
    budget: float = typer.Option(5.0, "--budget", help="USD; exit 4 if the estimate exceeds it"),
    question: Optional[str] = typer.Option(
        None, "--question", help="Question id to check (default: the first one answered)"
    ),
    final: bool = typer.Option(False, "--final", help="Allow a held-out *-test.yaml set"),
) -> None:
    """ONE answer and ONE judge call (billed), then the estimated cost of the judged runs.

    Exit codes: 1 truncated answer or verdict, judge error or LLM error; 2 unreviewed references or a
    held-out set without --final; 3 the model has no price (CODEATLAS_LLM_PRICES); 4 over budget."""
    from codeatlas.eval.dataset import load_eval_set
    from codeatlas.eval.judge_run import ESTIMATE_MARGIN, estimate_cost, single_check
    from codeatlas.llm import LLMError

    _require_final(eval_set, final)
    items = load_eval_set(eval_set)
    _require_reviewed(items)
    atlas = _atlas(ctx)
    try:
        cfg = atlas.setup_config(setup)
        check = single_check(atlas, items, cfg, question)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except LLMError as exc:
        typer.secho(f"LLM call failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"model {atlas.llm.name} | setup {setup} ({cfg.name}, k={cfg.k}) | question {check.question_id}")
    parts = (("answer", check.answer, check.answer_cost_usd), ("judge", check.judge, check.judge_cost_usd))
    for name, part, cost in parts:
        fields = " | ".join(f"{key}={value}" for key, value in part.items())
        typer.echo(f"{name:<6} {fields} | cost={'unpriced' if cost is None else _money(cost)}")
    if check.problems:
        typer.secho("check failed: " + "; ".join(check.problems), fg=typer.colors.RED)
        raise typer.Exit(1)

    estimate = estimate_cost(check, items, passes)
    if estimate is None:
        typer.secho(
            f"{atlas.llm.name} has no price, so the cost cannot be estimated or capped. Set CODEATLAS_LLM_PRICES.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(3)
    answerable = sum(1 for i in items if i.answerable)
    typer.echo(
        f"estimate: {passes} pass(es) x ({len(items)} answers + {answerable} verdicts) x {ESTIMATE_MARGIN} margin "
        f"= {_money(estimate)} (budget {_money(budget)})"
    )
    if estimate > budget:
        typer.secho("the estimate exceeds the budget", fg=typer.colors.RED)
        raise typer.Exit(4)


@app.command("judge-summary")
def judge_summary(
    reports: List[Path] = typer.Argument(..., exists=True, dir_okay=False, help="eval-*.json from judged runs"),
    budget: Optional[float] = typer.Option(
        None, "--budget", help="USD; exit 4 if the runs cost more (3 if unpriced)"
    ),
) -> None:
    """The README answer-quality table (mean ± sd over the runs) and their total LLM cost. Makes no calls."""
    from codeatlas.eval.judge_run import summarize, summary_markdown

    try:
        summary = summarize(reports)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(summary_markdown(summary))
    if budget is not None:
        cost = summary["total_cost_usd"]
        if cost is None:
            typer.secho("the runs are unpriced, so the budget cannot be enforced", fg=typer.colors.RED)
            raise typer.Exit(3)
        if cost > budget:
            typer.secho(f"spent {_money(cost)}, over the {_money(budget)} budget", fg=typer.colors.RED)
            raise typer.Exit(4)


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


@app.command()
def demo(
    ctx: typer.Context,
    repo: Path = typer.Option(Path("repos/spring-petclinic"), "--repo", help="Where PetClinic is, or will be cloned"),
    port: int = typer.Option(8000, help="Port for the API and the UI (on 127.0.0.1)"),
    ui: bool = typer.Option(True, "--ui/--no-ui", help="Build and serve the web UI (needs Node 24+)"),
    keep_llm: bool = typer.Option(
        False, "--keep-llm", help="Use the configured LLM provider instead of the offline fake one"
    ),
) -> None:
    """One command to a running demo: clone PetClinic at the evaluated revision if missing, index it, and serve
    the API and the web UI on http://127.0.0.1:PORT with the offline fake LLM (no keys, no paid calls)."""
    from codeatlas.demo import build_ui, ensure_repo

    def say(message: str) -> None:
        typer.secho(f"[demo] {message}", fg=typer.colors.CYAN)

    settings = _settings(ctx)
    try:
        ensure_repo(repo, say)
    except Exception as exc:  # noqa: BLE001 - git missing or the clone failed: explain and stop
        typer.secho(f"could not prepare {repo}: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    updates: dict = {"repo_path": repo.resolve()}
    if not keep_llm:
        updates.update(llm_provider="fake", llm_model="offline")
    if ui:
        try:
            ui_dir = build_ui(say)
        except Exception as exc:  # noqa: BLE001 - a failed UI build should not stop the API demo
            say(f"web UI build failed ({exc}); serving the API only")
            ui_dir = None
        if ui_dir is not None:
            updates["ui_dir"] = ui_dir
    settings = settings.model_copy(update=updates)
    ctx.obj = settings

    atlas = _atlas(ctx)
    say(f"indexing {repo} (only changed files are re-embedded) ...")
    stats = atlas.index(repo.resolve())
    say(f"{stats.mode} index: {stats.files_changed} of {stats.files_scanned} files changed, {stats.chunks_written} chunks embedded")

    import uvicorn

    from codeatlas.api.app import create_app

    where = f"http://127.0.0.1:{port}"
    say(f"LLM {settings.llm_provider}:{settings.llm_model} | API {where}/api | UI {where if 'ui_dir' in updates else 'not built'}")
    uvicorn.run(create_app(atlas=atlas), host="127.0.0.1", port=port)


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
