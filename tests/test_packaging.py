import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))


class PackagingTests(unittest.TestCase):
    def test_api_image_does_not_copy_full_rag_context(self):
        with open(os.path.join(ROOT, "Dockerfile.api"), encoding="utf-8") as f:
            dockerfile = f.read()
        self.assertNotIn("COPY . .", dockerfile)
        self.assertNotIn("chroma_db", dockerfile)

    def test_api_requirements_exclude_rag_and_openai(self):
        with open(os.path.join(ROOT, "requirements-api.txt"), encoding="utf-8") as f:
            requirements = f.read()
        self.assertNotIn("langchain", requirements)
        self.assertNotIn("openai", requirements)
        self.assertNotIn("httpx", requirements)


if __name__ == "__main__":
    unittest.main()
