import json
from pathlib import Path
from typing import Any

import httpx

from .config import OPENROUTER_URL, OPENROUTER_KEY, LLM_MODEL

try:
    from process_env import load_env_keys
except ImportError:
    try:
        from ..process_env import load_env_keys
    except ImportError:
        from src.process_env import load_env_keys

REQUEST_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


class LLMError(RuntimeError):
    """Base class for LLM client failures."""


class LLMTransportError(LLMError):
    """Network-level failure (DNS, connection, read timeout)."""


class LLMHTTPError(LLMError):
    """HTTP-level failure (non-2xx status or API error payload)."""


class LLMResponseError(LLMError):
    """Malformed or unusable response body."""


def load_openrouter_key() -> str:
    """Load OpenRouter API key from env or the dedicated meetcap env file."""
    if OPENROUTER_KEY:
        return OPENROUTER_KEY

    candidate = Path.home() / ".config" / "meetcap" / "env"
    keys = load_env_keys(candidate, allowed={"OPENROUTER_API_KEY"})
    return keys.get("OPENROUTER_API_KEY", "").strip()


def should_retry_without_structured_output(error: RuntimeError) -> bool:
    """Decide whether it is worth retrying without response_format."""
    message = str(error).lower()
    fatal_terms = (
        "no api key",
        "401",
        "403",
        "authorization",
        "insufficient",
        "rate limit",
        "quota",
    )
    return not any(term in message for term in fatal_terms)


def _build_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def call_openrouter(
    *,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float | None,
    reasoning_effort: str = "none",
    response_format: dict[str, Any] | None = None,
) -> str:
    """Call OpenRouter and return the assistant content."""
    api_key = load_openrouter_key()
    if not api_key:
        raise LLMError("no API key configured")

    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
        "reasoning": {"effort": reasoning_effort},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format is not None:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    with _build_client() as client:
        try:
            response = client.post(OPENROUTER_URL, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMTransportError(f"request failed: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        if response.is_error:
            raise LLMHTTPError(
                f"HTTP {response.status_code}: {response.text[:300]}"
            ) from exc
        raise LLMResponseError(
            f"invalid JSON response (HTTP {response.status_code}): {exc}"
        ) from exc

    error = body.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise LLMHTTPError(f"HTTP {response.status_code}: {message}")

    if response.is_error:
        raise LLMHTTPError(
            f"HTTP {response.status_code}: {response.text[:300]}"
        )

    choices = body.get("choices") or []
    if not choices:
        raise LLMResponseError("no choices returned")

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not content.strip():
        raise LLMResponseError("empty response content")

    return content.strip()
