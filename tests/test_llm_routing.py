import asyncio
import importlib
import json
import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeResponse:
    def __init__(self, text):
        self.output_text = text


class FakeResponses:
    def __init__(self):
        self.calls = []
        self.semantic_route = "off_topic"
        self.semantic_reply = "Este atendimento é voltado à produção e manejo de maçãs."
        self.reception_reply = "Boa tarde! Como posso ajudar com o seu pomar hoje?"
        self.technical_reply = "Para orientar com segurança, preciso de mais detalhes do pomar."

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("text"):
            return FakeResponse(
                json.dumps(
                    {"route": self.semantic_route, "reply": self.semantic_reply},
                    ensure_ascii=False,
                )
            )
        if "atendente inicial" in kwargs.get("instructions", ""):
            return FakeResponse(self.reception_reply)
        return FakeResponse(self.technical_reply)


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class LLMRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_memory = sys.modules.get("services.memory")
        cls.original_store = sys.modules.get("rag.store")

        fake_memory = types.ModuleType("services.memory")
        fake_memory.fetch_messages = lambda wa_from, limit=8: []
        fake_memory.build_context_block = lambda wa_from, max_history=12: ("", [], None)
        sys.modules["services.memory"] = fake_memory

        fake_store = types.ModuleType("rag.store")

        async def placeholder_retrieve(question):
            return []

        fake_store.retrieve_documents = placeholder_retrieve
        sys.modules["rag.store"] = fake_store
        sys.modules.pop("services.llm", None)
        cls.llm = importlib.import_module("services.llm")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("services.llm", None)
        if cls.original_memory is None:
            sys.modules.pop("services.memory", None)
        else:
            sys.modules["services.memory"] = cls.original_memory
        if cls.original_store is None:
            sys.modules.pop("rag.store", None)
        else:
            sys.modules["rag.store"] = cls.original_store

    def setUp(self):
        self.client = FakeClient()
        self.llm._openai_client = lambda: self.client
        self.retrieval_calls = []
        self.history = []

        async def fake_retrieve(question):
            self.retrieval_calls.append(question)
            return []

        self.llm.retrieve_documents = fake_retrieve
        self.llm.fetch_messages = lambda wa_from, limit=8: list(self.history)
        self.llm.build_context_block = lambda wa_from, max_history=12: (
            self.llm.SYSTEM_PROMPT,
            list(self.history),
            None,
        )

    def test_greeting_with_punctuation_never_retrieves(self):
        decision = self.llm.route_message("Boa tarde.")
        reply = asyncio.run(
            self.llm.handler_gpt5_rag("551199", "Boa tarde.", decision=decision)
        )
        self.assertEqual(self.retrieval_calls, [])
        self.assertIn("Boa tarde", reply)
        self.assertEqual(self.client.responses.calls[0]["model"], self.llm.GPT5_FAST_MODEL)

    def test_off_topic_semantic_route_never_retrieves(self):
        self.client.responses.semantic_route = "off_topic"
        self.client.responses.semantic_reply = "Posso ajudar com produção e manejo de maçãs."
        decision = self.llm.route_message("Qual é a capital da França?")
        reply = asyncio.run(
            self.llm.handler_gpt5_rag(
                "551199",
                "Qual é a capital da França?",
                decision=decision,
            )
        )
        self.assertEqual(self.retrieval_calls, [])
        self.assertIn("maçãs", reply)

    def test_semantic_apple_route_enables_rag_and_full_model(self):
        self.client.responses.semantic_route = "apple_technical"
        self.client.responses.semantic_reply = ""
        decision = self.llm.route_message("As folhas estão com sarna?")
        asyncio.run(
            self.llm.handler_gpt5_rag(
                "551199",
                "As folhas estão com sarna?",
                decision=decision,
            )
        )
        self.assertEqual(len(self.retrieval_calls), 1)
        self.assertEqual(self.client.responses.calls[-1]["model"], self.llm.GPT5_RAG_MODEL)

    def test_missing_evidence_is_answered_by_full_model_not_hard_stop(self):
        decision = self.llm.route_message("Como controlar a grafolita no pomar?")
        reply = asyncio.run(
            self.llm.handler_gpt5_rag(
                "551199",
                "Como controlar a grafolita no pomar?",
                decision=decision,
            )
        )
        self.assertEqual(len(self.retrieval_calls), 1)
        self.assertEqual(self.client.responses.calls[-1]["model"], self.llm.GPT5_RAG_MODEL)
        self.assertEqual(reply, self.client.responses.technical_reply)
        self.assertNotIn("base documental", reply.lower())


if __name__ == "__main__":
    unittest.main()
