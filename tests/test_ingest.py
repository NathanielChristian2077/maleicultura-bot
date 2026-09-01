import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rag.ingest import clean_chunk_text, contextualize_chunk, _metadata_from_chunk
from rag.lexical import initialize_lexical_index, search_lexical, upsert_lexical_documents


class IngestTests(unittest.TestCase):
    def test_clean_chunk_removes_consecutive_ocr_duplicates(self):
        raw = "Linha importante\n\nLinha importante\n\nOutra   linha"
        cleaned = clean_chunk_text(raw)
        self.assertEqual(cleaned.count("Linha importante"), 1)
        self.assertIn("Outra linha", cleaned)

    def test_metadata_uses_human_page_and_fallback_title(self):
        metadata = _metadata_from_chunk(
            {
                "chunk_id": "abc",
                "doc_id": "manual_maca.pdf",
                "fonte": "manual_maca.pdf",
                "pagina": 0,
            }
        )
        self.assertEqual(metadata["pagina"], 1)
        self.assertEqual(metadata["pagina_indice"], 0)
        self.assertEqual(metadata["titulo"], "manual_maca")
        self.assertEqual(metadata["chunk_id"], "abc")

    def test_contextualization_adds_document_context(self):
        text = contextualize_chunk(
            "Controle por monitoramento.",
            {"titulo": "Manejo", "fonte": "manual.pdf", "pagina": 7},
        )
        self.assertTrue(text.startswith("Título: Manejo"))
        self.assertIn("Página: 7", text)
        self.assertTrue(text.endswith("Controle por monitoramento."))


class LexicalIndexTests(unittest.TestCase):
    def test_fts_returns_exact_agronomic_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = initialize_lexical_index(tmp, rebuild=True)
            try:
                upsert_lexical_documents(
                    conn,
                    [
                        ("1", "Controle da grafolita com armadilhas.", {"chunk_id": "1"}),
                        ("2", "Manejo de irrigação no pomar.", {"chunk_id": "2"}),
                    ],
                )
            finally:
                conn.close()

            hits = search_lexical(tmp, "grafolita", 5)
            self.assertEqual([hit.chunk_id for hit in hits], ["1"])


if __name__ == "__main__":
    unittest.main()
