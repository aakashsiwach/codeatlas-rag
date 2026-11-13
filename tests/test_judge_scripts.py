"""scripts/run-judge.{sh,ps1} control flow, offline: the fake LLM with a made-up price, on the fixture repo."""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURE_EVAL

ROOT = Path(__file__).resolve().parents[1]
PRICES = '{"fake:offline": {"input_per_mtok": 1000, "output_per_mtok": 1000}}'  # inflated, so budgets bite


def _bash() -> str | None:
    if platform.system() == "Windows":  # never WSL's bash.exe: it would not see this virtualenv
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        return str(git_bash) if git_bash.exists() else None
    return shutil.which("bash")


def _powershell() -> str | None:
    return shutil.which("pwsh") or (shutil.which("powershell") if platform.system() == "Windows" else None)


SHELLS = {
    "sh": lambda exe, args: [exe, str(ROOT / "scripts" / "run-judge.sh"), *args],
    "ps1": lambda exe, args: [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                              str(ROOT / "scripts" / "run-judge.ps1"), *args],
}
FLAGS = {  # the same options in each script's own style
    "sh": {"passes": "--passes", "budget": "--budget", "eval": "--eval-set", "out": "--out", "yes": "--yes"},
    "ps1": {"passes": "-Passes", "budget": "-Budget", "eval": "-EvalSet", "out": "-Out", "yes": "-Yes"},
}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    work = tmp_path_factory.mktemp("judge-scripts")
    repo = work / "shop"
    shutil.copytree(ROOT / "tests" / "fixtures" / "shop", repo)
    environ = {
        **os.environ,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        "PYTHONIOENCODING": "utf-8",
        "CODEATLAS_INDEX_DIR": str(work / "index"),
        "CODEATLAS_EMBEDDING_PROVIDER": "hashing",
        "CODEATLAS_RERANKER_MODEL": "",
        "CODEATLAS_LLM_PROVIDER": "fake",
        "CODEATLAS_LLM_MODEL": "offline",
        "CODEATLAS_LLM_PRICES": PRICES,
        "CODEATLAS_MIN_VECTOR_SIMILARITY": "0",
        "CODEATLAS_SETUPS": '{"quality": {"mode": "hybrid+graph", "k": 3}}',
    }
    environ.pop("CODEATLAS_QDRANT_URL", None)  # the embedded store, shared by every process below
    subprocess.run([sys.executable, "-c", "from codeatlas.cli import app; app()", "index", str(repo)],
                   env=environ, cwd=work, check=True, capture_output=True)
    return environ, work


def _run(kind, env, *args, timeout=300):
    exe = _bash() if kind == "sh" else _powershell()
    if exe is None:
        pytest.skip(f"no shell for run-judge.{kind}")
    environ, work = env
    return subprocess.run(SHELLS[kind](exe, list(args)), env=environ, cwd=work, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


@pytest.mark.parametrize("kind", ["sh", "ps1"])
def test_runs_every_step_and_prints_the_table(kind, env):
    f = FLAGS[kind]
    out = env[1] / f"results-{kind}"
    result = _run(kind, env, f["eval"], str(FIXTURE_EVAL), f["passes"], "2", f["budget"], "1000",
                  f["out"], str(out), f["yes"])
    assert result.returncode == 0, result.stdout + result.stderr
    text = result.stdout
    for marker in ("1/6", "2/6", "3/6", "judged pass 1 of 2", "judged pass 2 of 2", "6/6"):
        assert marker in text, marker
    assert "llm call" in text and "estimate: 2 pass(es)" in text
    assert "| hybrid+graph | 2 | 1.000 ± 0.000 |" in text
    assert len(list(out.glob("judge-*/pass-*/eval-*.json"))) == 2


@pytest.mark.parametrize("kind", ["sh", "ps1"])
def test_refuses_unreviewed_references_before_any_call(kind, env):
    f = FLAGS[kind]
    unreviewed = env[1] / "unreviewed.yaml"
    unreviewed.write_text(
        FIXTURE_EVAL.read_text(encoding="utf-8").replace(
            "  - id: payment-retry\n", "  - id: payment-retry\n    reference_answer: In charge.\n    reviewed: false\n", 1
        ),
        encoding="utf-8",
    )
    result = _run(kind, env, f["eval"], str(unreviewed), f["yes"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert "payment-retry" in result.stdout + result.stderr
    assert "2/6" not in result.stdout  # stopped before the provider check


@pytest.mark.parametrize("kind", ["sh", "ps1"])
def test_stops_when_the_estimate_exceeds_the_budget(kind, env):
    f = FLAGS[kind]
    result = _run(kind, env, f["eval"], str(FIXTURE_EVAL), f["budget"], "0.0001", f["out"],
                  str(env[1] / "never"), f["yes"])
    assert result.returncode == 4, result.stdout + result.stderr
    assert "judged pass" not in result.stdout and not (env[1] / "never").exists()


def test_bash_asks_for_confirmation(env):
    exe = _bash()
    if exe is None:
        pytest.skip("no bash")
    environ, work = env
    result = subprocess.run(
        SHELLS["sh"](exe, ["--eval-set", str(FIXTURE_EVAL), "--budget", "1000", "--out", str(work / "declined")]),
        env=environ, cwd=work, input="n\n", capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert result.returncode == 1 and "aborted; nothing was run" in result.stdout
    assert not (work / "declined").exists()
