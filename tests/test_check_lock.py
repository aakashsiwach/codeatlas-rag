"""scripts/check_lock.py flags packages a full install would add without a matching pin."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_lock.py"


def item(name: str, version: str) -> dict:
    return {"metadata": {"name": name, "version": version}}


def run(tmp_path: Path, installs: list[dict], lock: str) -> subprocess.CompletedProcess:
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"install": installs}), encoding="utf-8")
    lock_file = tmp_path / "lock.txt"
    lock_file.write_text(lock, encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), str(report), str(lock_file)], capture_output=True, text=True)


def test_passes_when_everything_is_pinned(tmp_path):
    lock = "# header\nuuid_utils==0.19.0\nPyYAML==6.0.3\ntorch==2.15.0+cpu\n"
    installs = [item("codeatlas", "1.0.0"), item("uuid-utils", "0.19.0"), item("pyyaml", "6.0.3"), item("torch", "2.15.0+cpu")]
    result = run(tmp_path, installs, lock)
    assert result.returncode == 0, result.stdout
    assert "0 problem(s)" in result.stdout


def test_fails_on_unpinned_and_mismatched_packages(tmp_path):
    result = run(tmp_path, [item("setuptools", "84.2.1"), item("mpmath", "1.3.0")], "mpmath==1.4.1\n")
    assert result.returncode == 1
    assert "setuptools 84.2.1 is installed but not pinned" in result.stdout
    assert "mpmath would install 1.3.0, the lock pins 1.4.1" in result.stdout


def test_the_repository_lock_parses():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from check_lock import read_lock
    finally:
        sys.path.pop(0)
    pins = read_lock(ROOT / "requirements-lock.txt")
    assert pins["torch"].endswith("+cpu") and "setuptools" in pins and "mpmath" in pins
