"""Install a Chroma import stub before loading BR/UT agents in offline tests."""

import sys
from types import ModuleType


def install():
    existing = sys.modules.get("chromadb")
    if existing is not None and getattr(existing, "_checkpoint_offline_stub", False):
        return
    if existing is not None:
        raise RuntimeError(
            "A real chromadb module is already imported; the offline stub "
            "will not replace it."
        )

    chromadb = ModuleType("chromadb")
    chromadb._checkpoint_offline_stub = True

    class PersistentClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("offline tests must not open Chroma")

    chromadb.PersistentClient = PersistentClient
    sys.modules["chromadb"] = chromadb

    utils = ModuleType("chromadb.utils")
    sys.modules["chromadb.utils"] = utils

    embedding_functions = ModuleType("chromadb.utils.embedding_functions")

    class SentenceTransformerEmbeddingFunction:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("offline tests must not load embeddings")

    embedding_functions.SentenceTransformerEmbeddingFunction = (
        SentenceTransformerEmbeddingFunction
    )
    sys.modules["chromadb.utils.embedding_functions"] = embedding_functions


install()
