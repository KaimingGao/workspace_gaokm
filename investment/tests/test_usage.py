import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.llm_client import (
    LLMClient,
    _friendly_request_error,
    _is_transient_error,
    add_usage,
    empty_usage,
    format_usage,
    parse_usage,
)
from unittest.mock import patch
import requests


class TestUsage(unittest.TestCase):
    def test_parse_usage(self):
        u = parse_usage(
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "completion_tokens_details": {"reasoning_tokens": 20},
                }
            }
        )
        self.assertEqual(u["prompt_tokens"], 100)
        self.assertEqual(u["completion_tokens"], 50)
        self.assertEqual(u["total_tokens"], 150)
        self.assertEqual(u["reasoning_tokens"], 20)
        self.assertEqual(u["calls"], 1)

    def test_parse_missing(self):
        u = parse_usage({})
        self.assertEqual(u["total_tokens"], 0)
        self.assertEqual(u["calls"], 0)

    def test_add_and_format(self):
        a = empty_usage()
        add_usage(a, parse_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}))
        add_usage(a, parse_usage({"usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}}))
        self.assertEqual(a["prompt_tokens"], 30)
        self.assertEqual(a["total_tokens"], 40)
        self.assertEqual(a["calls"], 2)
        text = format_usage(a, prefix="本轮 token: ")
        self.assertIn("prompt=30", text)
        self.assertIn("total=40", text)
        self.assertIn("calls=2", text)

    def test_timeout_defaults_and_friendly_error(self):
        c = LLMClient(api_key="x")
        self.assertEqual(c.chat_timeout, 120.0)
        self.assertEqual(c.probe_timeout, 30.0)
        err = requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='dashscope.aliyuncs.com', port=443): Read timed out. (read timeout=10)"
        )
        self.assertTrue(_is_transient_error(err))
        self.assertIn("超时", _friendly_request_error(err))

    def test_qwen_defaults_and_search_body(self):
        with patch.dict(
            os.environ,
            {
                "DASHSCOPE_API_KEY": "sk-test",
                "DASHSCOPE_ENABLE_SEARCH": "1",
                "DASHSCOPE_SEARCH_STRATEGY": "max",
                "DASHSCOPE_SEARCH_FRESHNESS": "7",
                "DOUBAO_API_KEY": "",
                "DOUBAO_ENDPOINT": "",
                "DOUBAO_MODEL": "",
            },
            clear=False,
        ):
            c = LLMClient()
            self.assertEqual(c.model, "qwen-plus")
            self.assertTrue(c.enable_search)
            body = c._search_body(enable_search=True)
            self.assertTrue(body.get("enable_search"))
            self.assertEqual(body["search_options"]["search_strategy"], "max")
            self.assertEqual(body["search_options"]["freshness"], 7)

    def test_probe_timeout_does_not_lock_forever(self):
        c = LLMClient(api_key="x")
        with patch.object(c, "_test_connection", side_effect=requests.exceptions.ReadTimeout("timed out")):
            self.assertFalse(c.is_available())
            self.assertFalse(c._tested)
            self.assertIn("超时", c.get_last_error() or "")
        with patch.object(c, "_test_connection", return_value=None):
            self.assertTrue(c.is_available())
            self.assertTrue(c._available)


if __name__ == "__main__":
    unittest.main()
