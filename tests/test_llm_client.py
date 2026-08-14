import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from exporter import llm_client


def ok_response(content="summary text"):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self._client_patcher = patch(
            "exporter.llm_client._build_client",
            side_effect=self._build_client,
        )
        self._client_patcher.start()
        self.addCleanup(self._client_patcher.stop)
        self.key_patcher = patch("exporter.llm_client.OPENROUTER_KEY", "test-key")
        self.key_patcher.start()
        self.addCleanup(self.key_patcher.stop)
        self.handler = None

    def _build_client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def call(self, handler, **kwargs):
        self.handler = handler
        kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
        kwargs.setdefault("max_tokens", 100)
        kwargs.setdefault("temperature", 0.2)
        return llm_client.call_openrouter(**kwargs)

    def test_returns_assistant_content(self):
        result = self.call(lambda request: ok_response("  hello \n"))
        self.assertEqual(result, "hello")

    def test_raises_error_when_no_api_key(self):
        self.handler = lambda request: ok_response()
        with patch("exporter.llm_client.load_openrouter_key", return_value=""):
            with self.assertRaises(llm_client.LLMError) as ctx:
                llm_client.call_openrouter(
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=100,
                    temperature=0.2,
                )
        self.assertIn("no api key", str(ctx.exception).lower())

    def test_transport_failure_raises_transport_error(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        with self.assertRaises(llm_client.LLMTransportError):
            self.call(handler)

    def test_timeout_raises_transport_error(self):
        def handler(request):
            raise httpx.ReadTimeout("timed out")

        with self.assertRaises(llm_client.LLMTransportError):
            self.call(handler)

    def test_api_error_payload_raises_http_error_with_status(self):
        def handler(request):
            return httpx.Response(
                401,
                json={"error": {"message": "Invalid API key"}},
            )

        with self.assertRaises(llm_client.LLMHTTPError) as ctx:
            self.call(handler)
        self.assertIn("401", str(ctx.exception))
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_error_status_without_error_field_raises_http_error(self):
        def handler(request):
            return httpx.Response(502, text="bad gateway")

        with self.assertRaises(llm_client.LLMHTTPError) as ctx:
            self.call(handler)
        self.assertIn("502", str(ctx.exception))

    def test_invalid_json_raises_response_error(self):
        with self.assertRaises(llm_client.LLMResponseError):
            self.call(lambda request: httpx.Response(200, text="<html>oops</html>"))

    def test_missing_choices_raises_response_error(self):
        with self.assertRaises(llm_client.LLMResponseError):
            self.call(lambda request: httpx.Response(200, json={}))

    def test_empty_content_raises_response_error(self):
        with self.assertRaises(llm_client.LLMResponseError):
            self.call(lambda request: ok_response("   "))

    def test_sends_payload_with_model_and_messages(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return ok_response()

        self.call(
            handler,
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        self.assertEqual(captured["url"], llm_client.OPENROUTER_URL)
        self.assertEqual(captured["auth"], "Bearer test-key")
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertIn("model", captured["body"])


class RetryDecisionTests(unittest.TestCase):
    def test_fatal_errors_do_not_retry(self):
        for message in (
            "no API key configured",
            "HTTP 401: invalid key",
            "HTTP 403: forbidden",
            "authorization failed",
            "insufficient credits",
            "rate limit exceeded",
            "quota exceeded",
        ):
            with self.subTest(message=message):
                self.assertFalse(
                    llm_client.should_retry_without_structured_output(RuntimeError(message))
                )

    def test_other_errors_retry(self):
        for message in (
            "no choices returned",
            "HTTP 500: internal error",
            "invalid JSON response",
        ):
            with self.subTest(message=message):
                self.assertTrue(
                    llm_client.should_retry_without_structured_output(RuntimeError(message))
                )


if __name__ == "__main__":
    unittest.main()
