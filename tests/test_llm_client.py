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


class LoadOpenRouterKeyTests(unittest.TestCase):
    def test_env_key_takes_precedence(self):
        with patch("exporter.llm_client.OPENROUTER_KEY", "synthetic-env-key"), \
             patch("exporter.llm_client.load_env_keys") as mock_load:
            key = llm_client.load_openrouter_key()
            self.assertEqual(key, "synthetic-env-key")
            mock_load.assert_not_called()

    def test_fallback_uses_load_env_keys_with_strict_allowlist(self):
        with patch("exporter.llm_client.OPENROUTER_KEY", ""), \
             patch("exporter.llm_client.load_env_keys", return_value={"OPENROUTER_API_KEY": "synthetic-file-key"}) as mock_load:
            key = llm_client.load_openrouter_key()
            self.assertEqual(key, "synthetic-file-key")
            mock_load.assert_called()
            # Verify strict allowlist of ONLY OPENROUTER_API_KEY was passed
            for call_args in mock_load.call_args_list:
                allowed = call_args.kwargs.get("allowed")
                if allowed is None and len(call_args.args) > 1:
                    allowed = call_args.args[1]
                self.assertEqual(set(allowed), {"OPENROUTER_API_KEY"})

    def test_fallback_returns_empty_when_no_key_found(self):
        with patch("exporter.llm_client.OPENROUTER_KEY", ""), \
             patch("exporter.llm_client.load_env_keys", return_value={}):
            key = llm_client.load_openrouter_key()
            self.assertEqual(key, "")

    def test_fallback_reads_only_dedicated_meetcap_env(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            meetcap_env = fake_home / ".config" / "meetcap" / "env"
            hermes_env = fake_home / ".hermes" / ".env"
            meetcap_env.parent.mkdir(parents=True, exist_ok=True)
            hermes_env.parent.mkdir(parents=True, exist_ok=True)

            # Only the hermes file exists: it must NOT be read at all.
            hermes_env.write_text("OPENROUTER_API_KEY=synthetic-hermes\nOTHER_SECRET=leak\n")
            with patch("exporter.llm_client.OPENROUTER_KEY", ""), \
                 patch("pathlib.Path.home", return_value=fake_home):
                self.assertEqual(llm_client.load_openrouter_key(), "")

            # The dedicated meetcap env file is the single fallback source.
            meetcap_env.write_text("OPENROUTER_API_KEY=synthetic-meetcap\nOTHER_SECRET=leak\n")
            with patch("exporter.llm_client.OPENROUTER_KEY", ""), \
                 patch("pathlib.Path.home", return_value=fake_home):
                self.assertEqual(llm_client.load_openrouter_key(), "synthetic-meetcap")


if __name__ == "__main__":
    unittest.main()
