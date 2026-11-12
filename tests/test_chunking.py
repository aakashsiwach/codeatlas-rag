from conftest import FIXTURE_REPO

from codeatlas.endpoints import literal_path, paths_match
from codeatlas.ingest.docs import chunk_markdown, chunk_naive
from codeatlas.ingest.text import line_windows, pack_lines
from codeatlas.ingest.walker import classify
from codeatlas.retrieval.tokenize import code_tokens


def test_markdown_sections_carry_breadcrumbs():
    text = (FIXTURE_REPO / "README.md").read_text(encoding="utf-8")
    chunks = chunk_markdown("README.md", text)
    sections = [c.metadata["section"] for c in chunks]
    assert ["Shop", "Modules", "Orders"] in sections
    assert ["Shop", "Running locally"] in sections
    assert ["Shop", "Modules"] not in sections  # heading with no body of its own
    assert all("code fence" not in s[-1] for s in sections)

    orders = next(c for c in chunks if c.metadata["section"][-1] == "Orders")
    assert orders.start_line == 7
    assert "Section: Shop > Modules > Orders" in orders.embed_text


def test_naive_chunks_have_no_header():
    text = "\n".join(f"line {i}" for i in range(400))
    chunks = chunk_naive("a.java", text, "java", max_chars=500)
    assert len(chunks) > 1
    assert all(c.embed_text == c.text for c in chunks)
    assert chunks[0].start_line == 1
    assert all(b.start_line == a.end_line + 1 for a, b in zip(chunks, chunks[1:]))


def test_pack_and_window_lines_are_contiguous():
    lines = ["x" * 50] * 10
    pieces = pack_lines(lines, 1, 120, prefer_blank=False)
    assert pieces[0][0] == 1 and pieces[-1][1] == 10
    assert all(b[0] == a[1] + 1 for a, b in zip(pieces, pieces[1:]))

    windows = line_windows([str(i) for i in range(25)], 100, 10, overlap=2)
    assert windows[0][:2] == (100, 109)
    assert windows[1][0] == 108
    assert windows[-1][1] == 124


def test_code_tokens_split_identifiers():
    tokens = code_tokens("OrderService.findOrderById(id) GET /orders/{id}")
    for t in ("orderservice", "order", "service", "findorderbyid", "find", "/orders/{id}", "orders"):
        assert t in tokens
    assert "the" not in code_tokens("the payment")


def test_paths_match_templates_and_prefixes():
    assert paths_match("/orders/{id}", "/orders/{orderId}")
    assert paths_match("/orders/42", "/orders/{id}")
    assert not paths_match("/orders/{id}", "/orders")
    assert paths_match("/owners/{id}/edit", "/owners/", prefix=True)
    assert literal_path("redirect:/owners/") == "/owners/"
    assert literal_path("http://svc/orders/{id}") == "/orders/{id}"
    assert literal_path("owners/createOrUpdateOwnerForm") is None


def test_classify():
    assert classify("src/Main.java") == ("java", "java")
    assert classify("docs/adr/0001.md") == ("doc", "markdown")
    assert classify("pom.xml") == ("config", "xml")
    assert classify("image.png") is None
