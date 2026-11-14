"""Check that requirements-lock.txt pins everything an install with all extras would pull in.

Reads the JSON report of a dry-run install (pip install --dry-run --report <file> -c requirements-lock.txt
".[all,dev]") and fails if any package the install would add is missing from the lock or has a different
version. pip itself already fails the dry-run when the pins conflict. Usage:
    python scripts/check_lock.py <pip-report.json> [requirements-lock.txt]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[normalize(name)] = version.strip()
    return pins


def problems(report: dict, pins: dict[str, str]) -> list[str]:
    found = []
    for item in report.get("install", []):
        meta = item["metadata"]
        name, version = normalize(meta["name"]), meta["version"]
        if name == "codeatlas":
            continue
        if name not in pins:
            found.append(f"{meta['name']} {version} is installed but not pinned")
        elif pins[name] != version:
            found.append(f"{meta['name']} would install {version}, the lock pins {pins[name]}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    report = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    pins = read_lock(Path(argv[2] if len(argv) == 3 else "requirements-lock.txt"))
    found = problems(report, pins)
    for problem in found:
        print(f"::error file=requirements-lock.txt::{problem}")
    count = len(report.get("install", []))
    print(f"{count} packages checked, {len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
