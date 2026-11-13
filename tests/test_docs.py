"""Generated docs stay in sync with the code."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_docs_are_up_to_date():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_config_docs.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
