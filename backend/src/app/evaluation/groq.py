"""Groq ``JudgeClient`` (OpenAI-compatible Chat Completions API) and settings wiring.

Requests use JSON mode (``response_format: {"type": "json_object"}``) with temperature 0. JSON
mode only guarantees syntactically valid JSON on models that support it; the reply is still
validated by ``app.evaluation.judge``. A reply cut off by the token limit
(``finish_reason == "length"``) is treated as unusable.

The API key is a ``SecretStr`` and is only revealed when building the Authorization header;
error messages never include it or the response body.
"""

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.evaluation.judge import JudgeClient, JudgeSettings, JudgeUnavailableError, LLMJudge


class JudgeConfigurationError(ValueError):
    """The judge is enabled but not configured correctly."""


class GroqJudgeClient:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 2_048,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._transport = transport

    @property
    def model_id(self) -> str:
        return f"groq/{self._model}"

    def complete_json(self, system: str, user: str) -> str:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_completion_tokens": self._max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise JudgeUnavailableError(
                f"Groq request timed out after {self._timeout:g} s"
            ) from exc
        except httpx.HTTPError as exc:
            raise JudgeUnavailableError(f"Groq request failed: {type(exc).__name__}") from exc

        if response.status_code != 200:
            retry = response.headers.get("retry-after")
            raise JudgeUnavailableError(
                f"Groq returned HTTP {response.status_code}"
                + (f" (retry after {retry} s)" if retry else "")
            )
        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise JudgeUnavailableError("Groq response has an unexpected shape") from exc
        if finish_reason == "length":
            raise JudgeUnavailableError("Groq reply was truncated at the token limit")
        if not isinstance(content, str) or not content.strip():
            raise JudgeUnavailableError("Groq reply has no text content")
        return content


def judge_client_from_settings(settings: Settings) -> JudgeClient | None:
    """``None`` when no judge is configured (``APP_JUDGE_PROVIDER=none``)."""
    if settings.judge_provider == "none":
        return None
    if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value().strip():
        raise JudgeConfigurationError(
            "APP_JUDGE_PROVIDER=groq requires APP_GROQ_API_KEY (or GROQ_API_KEY)"
        )
    return GroqJudgeClient(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        base_url=settings.groq_base_url,
        timeout_seconds=settings.judge_timeout_seconds,
    )


def judge_from_settings(settings: Settings) -> LLMJudge | None:
    client = judge_client_from_settings(settings)
    if client is None:
        return None
    return LLMJudge(client, JudgeSettings(samples=settings.judge_samples))
