"""Discover and classify the files in a repository."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXTENSIONS: dict[str, tuple[str, str]] = {
    ".java": ("java", "java"),
    ".md": ("doc", "markdown"),
    ".markdown": ("doc", "markdown"),
    ".adoc": ("doc", "asciidoc"),
    ".rst": ("doc", "text"),
    ".txt": ("doc", "text"),
    ".pdf": ("doc", "pdf"),
    ".yml": ("config", "yaml"),
    ".yaml": ("config", "yaml"),
    ".properties": ("config", "properties"),
    ".sql": ("config", "sql"),
}
SPECIAL_NAMES: dict[str, tuple[str, str]] = {
    "pom.xml": ("config", "xml"),
    "build.gradle": ("config", "groovy"),
    "build.gradle.kts": ("config", "kotlin"),
    "Dockerfile": ("config", "docker"),
}


@dataclass(frozen=True)
class SourceFile:
    path: str  # POSIX path relative to the repo root
    abs_path: Path
    category: str  # java | doc | config
    language: str
    sha256: str


def classify(rel_path: str) -> tuple[str, str] | None:
    p = PurePosixPath(rel_path)
    if p.name in SPECIAL_NAMES:
        return SPECIAL_NAMES[p.name]
    return EXTENSIONS.get(p.suffix.lower())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_source_file(repo: Path, rel_path: str, exclude_dirs: list[str], max_bytes: int) -> SourceFile | None:
    """Return a SourceFile for rel_path, or None when it is excluded, unsupported or too large."""
    rel = PurePosixPath(rel_path.replace("\\", "/"))
    # Same directories as discover() skips, so a targeted (webhook) update indexes exactly what a full scan
    # would: excluded names and hidden directories (.git, .github, .idea, ...).
    if any(part in exclude_dirs or part.startswith(".") for part in rel.parts[:-1]):
        return None
    kind = classify(str(rel))
    abs_path = repo / Path(*rel.parts)
    if kind is None or not abs_path.is_file() or abs_path.stat().st_size > max_bytes:
        return None
    # A symlink (or a symlinked directory) may point outside the repository, e.g. at ~/.aws/credentials;
    # such files must never be indexed, shown or sent to an LLM.
    if not abs_path.resolve().is_relative_to(repo.resolve()):
        return None
    return SourceFile(str(rel), abs_path, kind[0], kind[1], file_sha256(abs_path))


def discover(repo: Path, exclude_dirs: list[str], max_bytes: int) -> list[SourceFile]:
    excluded = set(exclude_dirs)
    files: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded and not d.startswith("."))
        for name in sorted(filenames):
            rel = Path(dirpath, name).relative_to(repo).as_posix()
            sf = load_source_file(repo, rel, exclude_dirs, max_bytes)
            if sf is not None:
                files.append(sf)
    return files
