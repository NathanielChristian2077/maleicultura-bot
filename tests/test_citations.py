import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rag.citations import append_sources


class FakeDocument:
    def __init__(self, metadata):
        self.metadata = metadata


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            FakeDocument({"titulo": "Manejo da Grafolita", "fonte": "manual.pdf", "pagina": 4}),
            FakeDocument({"titulo": "Irrigação", "fonte": "irrigacao.pdf", "pagina": 8}),
        ]

    def test_only_referenced_sources_are_appended(self):
        result = append_sources("Use armadilhas [1].", self.docs)
        self.assertIn("[1] Manejo da Grafolita", result)
        self.assertNotIn("[2] Irrigação", result)

    def test_all_sources_are_appended_when_model_omits_markers(self):
        result = append_sources("Use armadilhas.", self.docs)
        self.assertIn("[1] Manejo da Grafolita", result)
        self.assertIn("[2] Irrigação", result)


if __name__ == "__main__":
    unittest.main()
