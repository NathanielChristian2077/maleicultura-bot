import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.router import build_retrieval_query, route_message


class RouterTests(unittest.TestCase):
    def test_greeting_skips_rag_and_model(self):
        decision = route_message("Bom dia")
        self.assertEqual(decision.kind, "social")
        self.assertFalse(decision.use_rag)
        self.assertEqual(decision.model_tier, "none")
        self.assertIsNotNone(decision.static_reply)

    def test_simple_technical_uses_fast_rag(self):
        decision = route_message("Como controlar a grafolita no pomar?")
        self.assertEqual(decision.kind, "technical")
        self.assertTrue(decision.use_rag)
        self.assertEqual(decision.model_tier, "fast")
        self.assertFalse(decision.include_history)
        self.assertLessEqual(decision.max_output_tokens, 160)

    def test_followup_uses_previous_user_turn_for_retrieval(self):
        decision = route_message("E para a Fuji?")
        history = [
            {"role": "user", "content": "Como controlar sarna em Gala?"},
            {"role": "assistant", "content": "Resposta anterior"},
        ]
        query = build_retrieval_query("E para a Fuji?", history, decision)
        self.assertIn("Como controlar sarna em Gala?", query)
        self.assertIn("E para a Fuji?", query)
        self.assertTrue(decision.include_history)

    def test_complex_question_uses_full_model(self):
        decision = route_message(
            "Compare as vantagens e desvantagens do controle químico e biológico "
            "para grafolita e explique quando usar cada estratégia."
        )
        self.assertEqual(decision.kind, "complex")
        self.assertEqual(decision.model_tier, "full")
        self.assertTrue(decision.include_history)


if __name__ == "__main__":
    unittest.main()
