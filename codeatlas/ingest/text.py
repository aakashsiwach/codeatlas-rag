"""Line-oriented splitting helpers shared by the code, doc and naive chunkers."""

from __future__ import annotations


def split_lines(text: str) -> list[str]:
    """Split on newline only (dropping a trailing carriage return), the way tree-sitter and editors
    number lines.

    str.splitlines() also breaks on form feeds, U+2028 and other separators that can occur in comments or
    string literals, which would shift chunk text away from the line numbers reported for it."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def line_windows(
    lines: list[str], start_line: int, max_lines: int, overlap: int = 0
) -> list[tuple[int, int, str]]:
    """Split lines into windows of at most max_lines. Returns (start, end, text) with 1-based lines."""
    if len(lines) <= max_lines:
        return [(start_line, start_line + max(len(lines), 1) - 1, "\n".join(lines))]
    step = max(1, max_lines - overlap)
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        seg = lines[i : i + max_lines]
        out.append((start_line + i, start_line + i + len(seg) - 1, "\n".join(seg)))
        if i + max_lines >= len(lines):
            break
        i += step
    return out


def pack_lines(
    lines: list[str], start_line: int, max_chars: int, prefer_blank: bool = True
) -> list[tuple[int, int, str]]:
    """Pack lines into pieces of roughly max_chars, cutting at blank lines when prefer_blank is set."""
    out: list[tuple[int, int, str]] = []
    buf: list[str] = []
    buf_start = start_line
    size = 0
    for line in lines:
        buf.append(line)
        size += len(line) + 1
        if size < max_chars:
            continue
        cut = len(buf)
        if prefer_blank:
            for j in range(len(buf) - 1, 0, -1):
                if not buf[j].strip():
                    cut = j
                    break
        head, buf = buf[:cut], buf[cut:]
        if any(h.strip() for h in head):
            out.append((buf_start, buf_start + len(head) - 1, "\n".join(head)))
        buf_start += len(head)
        size = sum(len(b) + 1 for b in buf)
    if any(b.strip() for b in buf):
        out.append((buf_start, buf_start + len(buf) - 1, "\n".join(buf)))
    return out


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 characters per token) used for cost tracking."""
    return max(1, len(text) // 4)
