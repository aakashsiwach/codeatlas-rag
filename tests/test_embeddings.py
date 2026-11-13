"""The local sentence-transformers embedder, with a fake model."""

import pytest


def test_local_embedder_dimension_with_new_and_old_sentence_transformers():
    from codeatlas.embeddings import SentenceTransformerEmbedder

    embedder = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)  # skip loading a model
    embedder.name = "local:fake"

    class New:
        def get_embedding_dimension(self):
            return 384

        def get_sentence_embedding_dimension(self):  # deprecated alias: must not be used
            raise AssertionError("old name used")

    class Old:
        def get_sentence_embedding_dimension(self):
            return 768

    class Variable:
        def get_embedding_dimension(self):
            return None

    embedder._model = New()
    assert embedder.dim == 384
    embedder._model = Old()
    assert embedder.dim == 768
    embedder._model = Variable()
    with pytest.raises(ValueError, match="fixed embedding dimension"):
        _ = embedder.dim
