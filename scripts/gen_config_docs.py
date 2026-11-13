"""Generate docs/configuration.md from codeatlas/config.py (field names, defaults and comments).

    python scripts/gen_config_docs.py          # write docs/configuration.md
    python scripts/gen_config_docs.py --check  # exit 1 if it is out of date (tests run this)
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "codeatlas" / "config.py"
OUT = ROOT / "docs" / "configuration.md"

HEADER = """# Configuration

Every setting is an environment variable with the `CODEATLAS_` prefix, or a line in a `.env` file in the
working directory (`.env.example` is a starting point). Lists and dicts are JSON, e.g.
`CODEATLAS_MIN_CONFIDENCE_BY_MODEL='{"local:BAAI/bge-small-en-v1.5": 0.6}'`. Process environment variables
win over `.env`.

Provider credentials are not CodeAtlas settings: they come from each SDK's standard sources
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `~/.aws` profiles), never from files in the repository.

This page is generated from `codeatlas/config.py` by `scripts/gen_config_docs.py`. Do not edit it by hand.
"""


def _comments(source: str) -> dict[int, str]:
    """Line number -> comment text, for every comment in the file."""
    found = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            found[tok.start[0]] = tok.string.lstrip("#").strip()
    return found


def _default(node: ast.expr, source: str) -> str:
    text = ast.get_source_segment(source, node) or ""
    if text == "DEFAULT_EXCLUDES":
        module = ast.parse(source)
        for stmt in module.body:
            if isinstance(stmt, ast.Assign) and getattr(stmt.targets[0], "id", None) == "DEFAULT_EXCLUDES":
                text = ast.get_source_segment(source, stmt.value) or text
    text = re.sub(r"\s+", " ", text).replace("[ ", "[").replace(" ]", "]").replace("{ ", "{").replace(", }", "}")
    text = text.replace(",]", "]").replace(",}", "}").replace("Path(", "").replace('")', '"')
    return text.replace("_000_000", "000000").replace("|", "\\|")


def generate() -> str:
    source = CONFIG.read_text(encoding="utf-8")
    comments = _comments(source)
    lines = source.splitlines()
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    out = [HEADER]
    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        if name == "model_config":
            continue
        # The section header and the description: comment lines directly above, then an end-of-line comment.
        above, line = [], stmt.lineno - 1
        while line >= 1 and lines[line - 1].strip().startswith("#"):
            text = comments.get(line, "")
            if text.startswith("==") and text.endswith("=="):
                out.append(f"\n## {text.strip('= ').strip()}\n\n| Variable | Default | Meaning |\n|---|---|---|")
                break
            above.insert(0, text)
            line -= 1
        inline = comments.get(stmt.lineno)
        description = " ".join([*above, *([inline] if inline else [])]).replace("|", "\\|")
        if not description:
            raise SystemExit(f"config.py: {name} has no comment; document it before regenerating")
        default = "unset" if stmt.value is None else _default(stmt.value, source)
        default = "unset" if default == "None" else f"`{default}`"
        out.append(f"| `CODEATLAS_{name.upper()}` | {default} | {description} |")
    return "\n".join(out) + "\n"


def main() -> int:
    text = generate()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python scripts/gen_config_docs.py")
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
