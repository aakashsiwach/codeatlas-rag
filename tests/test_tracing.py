"""Per-query traces: JSON lines, rotated by size."""

import json

from codeatlas.tracing import Tracer


def test_traces_rotate_at_the_size_limit(tmp_path):
    path = tmp_path / "traces.jsonl"
    tracer = Tracer(path, max_bytes=300)
    for i in range(12):
        tracer.finish(tracer.start("ask", question=f"question {i} " + "x" * 40))
    rotated = tmp_path / "traces.jsonl.1"
    assert rotated.exists() and path.stat().st_size < 300 + 200
    current = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    older = [json.loads(line) for line in rotated.read_text(encoding="utf-8").splitlines()]
    assert current and older and current[-1]["question"].startswith("question 11")
    assert not (tmp_path / "traces.jsonl.2").exists()  # only one old file is kept


def test_no_rotation_when_disabled_and_no_file_when_tracing_is_off(tmp_path):
    tracer = Tracer(tmp_path / "t.jsonl", max_bytes=0)
    for _ in range(20):
        tracer.finish(tracer.start("ask", question="x" * 50))
    assert not (tmp_path / "t.jsonl.1").exists()
    off = Tracer(None)
    off.finish(off.start("ask"))  # nothing written, no error
