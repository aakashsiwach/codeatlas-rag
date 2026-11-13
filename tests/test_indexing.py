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


def _link_dir(link, target) -> None:
    """Directory symlink, or a junction on Windows without symlink privilege."""
    import os
    import sys

    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_links_that_leave_the_repo_are_not_indexed(tmp_path, repo):
    from codeatlas.ingest.walker import discover, load_source_file

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "credentials.txt").write_text("aws_secret_access_key=abc", encoding="utf-8")
    _link_dir(repo / "docs" / "ext", outside)  # a linked directory pointing outside the repository
    _link_dir(repo / "docs" / "same", repo / "docs" / "adr")  # a linked directory that stays inside

    paths = {f.path for f in discover(repo, [], 1_000_000)}
    assert "docs/ext/credentials.txt" not in paths
    assert load_source_file(repo, "docs/ext/credentials.txt", [], 1_000_000) is None  # webhook path too
    assert load_source_file(repo, "docs/same/0001-payment-retries.md", [], 1_000_000) is not None


def test_a_locked_embedded_index_gives_a_clear_error(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from codeatlas.cli import app
    from codeatlas.config import Settings
    from codeatlas.storage.vector_store import IndexLockedError, VectorStore

    settings = Settings(_env_file=None, index_dir=tmp_path / "idx", qdrant_url=None)
    holder = VectorStore.from_settings(settings, 8)  # e.g. a running `codeatlas serve`
    with pytest.raises(IndexLockedError, match="in use by another CodeAtlas process"):
        VectorStore.from_settings(settings, 8)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEATLAS_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("CODEATLAS_RERANKER_MODEL", "")
    monkeypatch.delenv("CODEATLAS_QDRANT_URL", raising=False)
    result = CliRunner().invoke(app, ["--index-dir", str(tmp_path / "idx"), "stats"])
    assert result.exit_code == 3 and "--index-dir" in result.output and "Traceback" not in result.output
    holder.client.close()


def test_targeted_and_full_indexing_skip_the_same_directories(tmp_path):
    from codeatlas.ingest.walker import discover, load_source_file

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push", encoding="utf-8")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "Gen.java").write_text("class Gen {}", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("class App {}", encoding="utf-8")
    excludes = ["target"]

    full = {sf.path for sf in discover(tmp_path, excludes, 1_000_000)}
    targeted = {p for p in (".github/workflows/ci.yml", "target/Gen.java", "src/App.java")
                if load_source_file(tmp_path, p, excludes, 1_000_000) is not None}
    assert full == targeted == {"src/App.java"}
