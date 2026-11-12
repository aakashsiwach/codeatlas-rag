import pytest
from conftest import JAVA_ROOT

from codeatlas.atlas import CodeAtlas
from codeatlas.embeddings import HashingEmbedder
from codeatlas.ingest.indexer import IndexMismatchError


def test_full_then_incremental_indexing(settings, repo):
    atlas = CodeAtlas(settings, embedder=HashingEmbedder())
    first = atlas.index(repo)
    assert first.mode == "full"  # nothing indexed yet, so the first run is a full build
    assert first.files_scanned > 0
    assert first.files_changed == first.files_scanned
    assert first.chunks_written == atlas.store.count_chunks() == atlas.vectors.count()
    assert first.embed_tokens > 0
    assert not first.errors

    second = atlas.index(repo)
    assert second.mode == "incremental"
    assert second.files_changed == 0
    assert second.chunks_written == 0
    assert second.files_unchanged == first.files_scanned

    service = repo / JAVA_ROOT / "payment" / "PaymentService.java"
    service.write_text(service.read_text(encoding="utf-8").replace("three attempts", "five attempts"), encoding="utf-8")
    (repo / JAVA_ROOT / "billing" / "InvoiceJob.java").unlink()

    third = atlas.index(repo)
    assert third.files_changed == 1
    assert third.files_deleted == 1
    assert not atlas.store.chunks_for_path(f"{JAVA_ROOT}/billing/InvoiceJob.java")
    assert "com.example.shop.billing.InvoiceJob" not in atlas.graph.g
    assert atlas.store.count_chunks() == atlas.vectors.count()
    assert len(atlas.store.runs()) == 3


def test_targeted_reindex(settings, repo):
    atlas = CodeAtlas(settings, embedder=HashingEmbedder())
    atlas.index(repo)
    rel = f"{JAVA_ROOT}/order/Order.java"
    path = repo / rel
    path.write_text(path.read_text(encoding="utf-8") + "\n// touched\n", encoding="utf-8")

    stats = atlas.index(repo, only_paths=[rel, "README.md", "does/not/exist.java"])
    assert stats.mode == "targeted"
    assert stats.files_scanned == 2
    assert stats.files_changed == 1
    assert stats.files_deleted == 0


def test_chunker_change_requires_force(settings, repo):
    atlas = CodeAtlas(settings, embedder=HashingEmbedder())
    atlas.index(repo)
    with pytest.raises(IndexMismatchError):
        atlas.index(repo, chunker="naive")

    stats = atlas.index(repo, chunker="naive", force=True)
    assert stats.mode == "full"
    assert {c.kind for c in atlas.store.all_chunks()} == {"naive"}
    assert atlas.graph.stats()["types"] == 0  # the naive baseline has no structural facts
