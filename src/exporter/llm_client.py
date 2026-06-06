import json
import subprocess
from pathlib import Path
from typing import Any
from .config import OPENROUTER_URL, OPENROUTER_KEY, LLM_MODEL

def load_openrouter_key() -> str:
    """Load OpenRouter API key from Hermes .env if not in env."""
    if OPENROUTER_KEY:
        return OPENROUTER_KEY

    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""

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
        raise RuntimeError("no API key configured")

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

    result = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "120",
            OPENROUTER_URL,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ],
        capture_output=True,
        text=True,
        timeout=130,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")

    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc

    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message)

    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("no choices returned")

    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if not content.strip():
        raise RuntimeError("empty response content")

    return content.strip()
