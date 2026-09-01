import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rag.lexical import LexicalHit
from rag.store import _fuse_results


class FakeDocument:
    def __init__(self, page_content: str, metadata=None):
        self.page_content = page_content
        self.metadata = metadata or {}


class StoreFusionTests(unittest.TestCase):
    def setUp(self):
        fake_documents = types.ModuleType("langchain_core.documents")
        fake_documents.Document = FakeDocument
        fake_core = types.ModuleType("langchain_core")
        sys.modules["langchain_core"] = fake_core
        sys.modules["langchain_core.documents"] = fake_documents

    def test_dense_and_lexical_agreement_ranks_first(self):
        dense = [
            (FakeDocument("grafolita", {"chunk_id": "a"}), 0.4),
            (FakeDocument("irrigacao", {"chunk_id": "b"}), 0.3),
        ]
        lexical = [
            LexicalHit("a", "grafolita", {"chunk_id": "a"}, 1),
            LexicalHit("c", "grafolita monitoramento", {"chunk_id": "c"}, 2),
        ]
        docs = _fuse_results(dense, lexical)
        self.assertEqual(docs[0].metadata["chunk_id"], "a")
        self.assertEqual(docs[0].metadata["retrieval_channels"], "dense,lexical")
        self.assertLessEqual(len(docs), 3)

    def test_low_dense_candidate_without_lexical_support_is_rejected(self):
        dense = [(FakeDocument("ruido", {"chunk_id": "noise"}), 0.01)]
        docs = _fuse_results(dense, [])
        self.assertEqual(docs, [])


if __name__ == "__main__":
    unittest.main()
