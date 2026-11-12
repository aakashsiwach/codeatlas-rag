"""Heading-aware chunking for Markdown/AsciiDoc, plus plain-text, PDF and naive baselines."""

from __future__ import annotations

import re
from pathlib import Path

from codeatlas.ingest.text import pack_lines
from codeatlas.models import Chunk, make_chunk_id

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_ADOC_HEADING = re.compile(r"^(={1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def chunk_markdown(path: str, text: str, max_chars: int = 2400, language: str = "markdown") -> list[Chunk]:
    """Split a document into sections by heading; each chunk carries its heading breadcrumb."""
    heading_re = _ADOC_HEADING if language == "asciidoc" else _MD_HEADING
    sections: list[tuple[list[str], int, list[str]]] = []
    crumbs: list[str] = []
    current: tuple[list[str], int, list[str]] = ([], 1, [])
    in_fence = False

    for i, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else heading_re.match(line)
        if match:
            _push_section(sections, current)
            level = len(match.group(1))
            crumbs = crumbs[: level - 1] + [match.group(2).strip()]
            current = (list(crumbs), i, [line])
        else:
            current[2].append(line)
    _push_section(sections, current)

    chunks: list[Chunk] = []
    for section_crumbs, start, lines in sections:
        breadcrumb = " > ".join(section_crumbs)
        for part, (s, e, body) in enumerate(pack_lines(lines, start, max_chars)):
            header = f"Document: {path}" + (f"\nSection: {breadcrumb}" if breadcrumb else "")
            chunks.append(
                Chunk(
                    id=make_chunk_id(path, "doc", breadcrumb, s, part),
                    path=path,
                    kind="doc",
                    text=body,
                    embed_text=f"{header}\n\n{body}",
                    start_line=s,
                    end_line=e,
                    language=language,
                    metadata={"section": section_crumbs},
                )
            )
    return chunks


def _push_section(sections: list, section: tuple[list[str], int, list[str]]) -> None:
    crumbs, _, lines = section
    body = lines[1:] if crumbs else lines  # first line is the heading itself
    if any(line.strip() for line in body):
        sections.append(section)


def chunk_plain(path: str, text: str, kind: str, language: str, max_chars: int = 2000) -> list[Chunk]:
    """Blank-line-aware windows for config files, plain text and unparseable code."""
    chunks: list[Chunk] = []
    for part, (s, e, body) in enumerate(pack_lines(text.splitlines(), 1, max_chars)):
        chunks.append(
            Chunk(
                id=make_chunk_id(path, kind, None, s, part),
                path=path,
                kind=kind,
                text=body,
                embed_text=f"File: {path}\n\n{body}",
                start_line=s,
                end_line=e,
                language=language,
            )
        )
    return chunks


def chunk_naive(path: str, text: str, language: str, max_chars: int = 1500) -> list[Chunk]:
    """Baseline: fixed-size character windows with no structure and no contextual header."""
    chunks: list[Chunk] = []
    for part, (s, e, body) in enumerate(pack_lines(text.splitlines(), 1, max_chars, prefer_blank=False)):
        chunks.append(
            Chunk(
                id=make_chunk_id(path, "naive", None, s, part),
                path=path,
                kind="naive",
                text=body,
                embed_text=body,
                start_line=s,
                end_line=e,
                language=language,
            )
        )
    return chunks


def chunk_pdf(path: str, abs_path: Path, max_chars: int = 2400) -> list[Chunk]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("PDF support needs the pdf extra: pip install 'codeatlas[pdf]'") from exc

    chunks: list[Chunk] = []
    for page_no, page in enumerate(PdfReader(str(abs_path)).pages, start=1):
        lines = (page.extract_text() or "").splitlines()
        for part, (s, e, body) in enumerate(pack_lines(lines, 1, max_chars)):
            chunks.append(
                Chunk(
                    id=make_chunk_id(path, "doc", f"page-{page_no}", s, part),
                    path=path,
                    kind="doc",
                    text=body,
                    embed_text=f"Document: {path}\nPage: {page_no}\n\n{body}",
                    start_line=s,
                    end_line=e,
                    language="pdf",
                    metadata={"page": page_no},
                )
            )
    return chunks
