import importlib
import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeDynamoDB:
    def __init__(self):
        self.request = None

    def batch_write_item(self, **kwargs):
        self.request = kwargs
        return {"UnprocessedItems": {}}

    def put_item(self, **kwargs):
        return {}

    def query(self, **kwargs):
        return {"Items": []}


class FakeClientError(Exception):
    pass


class MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ddb = FakeDynamoDB()
        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda name: cls.ddb
        fake_botocore = types.ModuleType("botocore")
        fake_exceptions = types.ModuleType("botocore.exceptions")
        fake_exceptions.ClientError = FakeClientError
        sys.modules["boto3"] = fake_boto3
        sys.modules["botocore"] = fake_botocore
        sys.modules["botocore.exceptions"] = fake_exceptions
        sys.modules.pop("services.memory", None)
        cls.memory = importlib.import_module("services.memory")

    def test_exchange_is_written_in_one_batch_with_ordered_timestamps(self):
        self.memory.save_exchange("551199", "pergunta", "resposta")
        request_items = self.ddb.request["RequestItems"][self.memory.CONV_TABLE]
        self.assertEqual(len(request_items), 2)
        first = request_items[0]["PutRequest"]["Item"]
        second = request_items[1]["PutRequest"]["Item"]
        self.assertEqual(first["role"]["S"], "user")
        self.assertEqual(second["role"]["S"], "assistant")
        self.assertEqual(int(second["ts"]["N"]), int(first["ts"]["N"]) + 1)


if __name__ == "__main__":
    unittest.main()
