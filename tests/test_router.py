import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.router import build_retrieval_query, decision_from_semantic_route, route_message


class RouterTests(unittest.TestCase):
    def test_greeting_variants_never_use_rag(self):
        variants = [
            "Boa tarde",
            "boa tarde.",
            "BOA TARDE!!!",
            "oi",
            "oiê",
            "oiêêê",
            "Olá 👋",
            "Bom dia, tudo bem?",
        ]
        for text in variants:
            with self.subTest(text=text):
                decision = route_message(text)
                self.assertEqual(decision.kind, "social")
                self.assertFalse(decision.use_rag)
                self.assertEqual(decision.model_tier, "reception")

    def test_unknown_message_fails_closed_without_rag(self):
        for text in [
            "As folhas estão estranhas",
            "Como funciona irrigação?",
            "Qual é a capital da França?",
        ]:
            with self.subTest(text=text):
                decision = route_message(text)
                self.assertEqual(decision.kind, "ambiguous")
                self.assertFalse(decision.use_rag)

    def test_apple_technical_goes_directly_to_full_model_and_rag(self):
        decision = route_message("Como controlar a grafolita no pomar?")
        self.assertEqual(decision.kind, "technical")
        self.assertTrue(decision.use_rag)
        self.assertEqual(decision.model_tier, "full")
        self.assertEqual(decision.reasoning_effort, "minimal")

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

    def test_complex_apple_question_uses_more_reasoning(self):
        decision = route_message(
            "Compare as vantagens e desvantagens do controle químico e biológico "
            "para grafolita no pomar e explique quando usar cada estratégia."
        )
        self.assertEqual(decision.kind, "complex")
        self.assertEqual(decision.model_tier, "full")
        self.assertEqual(decision.reasoning_effort, "low")

    def test_semantic_routes_only_enable_rag_for_apple_intents(self):
        self.assertTrue(decision_from_semantic_route("apple_technical").use_rag)
        self.assertTrue(decision_from_semantic_route("apple_followup").use_rag)
        for route in ("social", "clarify", "off_topic", "unexpected"):
            with self.subTest(route=route):
                self.assertFalse(decision_from_semantic_route(route).use_rag)


if __name__ == "__main__":
    unittest.main()
