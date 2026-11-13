"""`codeatlas demo`: one command from a fresh clone to the UI, offline.

Clones Spring PetClinic at the evaluated revision if it is missing, indexes it (incrementally), builds the
web UI if Node is available, and serves the API and the UI on one port with the offline `fake` LLM.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

PETCLINIC_URL = "https://github.com/spring-projects/spring-petclinic"
PETCLINIC_REF = "500158f"  # the revision the evaluation sets were verified against
UI_SOURCE = Path(__file__).resolve().parents[1] / "ui"

Run = Callable[..., subprocess.CompletedProcess]


def ensure_repo(path: Path, say: Callable[[str], None], run: Run = subprocess.run) -> None:
    """Clone PetClinic at PETCLINIC_REF into `path` unless something is already there (never modified)."""
    if path.exists():
        head = run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        rev = head.stdout.strip() if head.returncode == 0 else "unknown"
        note = "" if rev.startswith(PETCLINIC_REF) else f" (evaluated revision is {PETCLINIC_REF}; left unchanged)"
        say(f"using existing {path} at {rev}{note}")
        return
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed; clone the repository yourself and pass --repo")
    say(f"cloning {PETCLINIC_URL} into {path} ...")
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--quiet", PETCLINIC_URL, str(path)], check=True)
    run(["git", "-C", str(path), "checkout", "--quiet", PETCLINIC_REF], check=True)


def build_ui(say: Callable[[str], None], source: Path = UI_SOURCE, run: Run = subprocess.run) -> Path | None:
    """The built UI directory, building it first if needed. None when there is no UI source or no npm."""
    dist = source / "dist"
    if (dist / "index.html").is_file():
        return dist
    npm = shutil.which("npm")
    if not (source / "package.json").is_file() or npm is None:
        say("web UI not built (needs the ui/ sources and Node 26+); serving the API only")
        return None
    say("building the web UI (npm ci, vite build) ...")
    if not (source / "node_modules").is_dir():
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=source, check=True)
    run([npm, "run", "build"], cwd=source, check=True)
    return dist if (dist / "index.html").is_file() else None
