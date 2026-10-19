"""Groq judge client against a mocked HTTP transport (no network) and settings wiring."""

import json
import re

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.evaluation.groq import (
    GroqJudgeClient,
    JudgeConfigurationError,
    judge_client_from_settings,
    judge_from_settings,
)
from app.evaluation.judge import JudgeUnavailableError

KEY = "gsk_test_not_a_real_key_0123456789"


def client_with(handler: httpx.MockTransport) -> GroqJudgeClient:
    return GroqJudgeClient(
        api_key=SecretStr(KEY), model="llama-3.3-70b-versatile", transport=handler
    )


def reply(content: object, finish_reason: str = "stop", status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]},
    )


def test_request_uses_json_mode_and_returns_the_reply_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply('{"ok": true}')

    client = client_with(httpx.MockTransport(handler))
    assert client.complete_json("system text", "user text") == '{"ok": true}'
    assert client.model_id == "groq/llama-3.3-70b-versatile"

    [request] = seen
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0
    assert body["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(429, headers={"retry-after": "7"}, text=KEY), "HTTP 429 (retry after 7 s)"),
        (httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}}), "HTTP 401"),
        (httpx.Response(200, text="<html>"), "unexpected shape"),
        (httpx.Response(200, json={"choices": []}), "unexpected shape"),
        (reply('{"dimensions": [', finish_reason="length"), "truncated at the token limit"),
        (reply(""), "no text content"),
        (reply(None), "no text content"),
    ],
)
def test_provider_failures_become_unavailable_errors_without_leaking_the_key(
    response: httpx.Response, message: str
) -> None:
    client = client_with(httpx.MockTransport(lambda request: response))
    with pytest.raises(JudgeUnavailableError, match=re.escape(message)) as excinfo:
        client.complete_json("s", "u")
    assert KEY not in str(excinfo.value)


def test_timeouts_and_connection_errors_are_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(JudgeUnavailableError, match="timed out after 30 s"):
        client_with(httpx.MockTransport(timeout)).complete_json("s", "u")
    with pytest.raises(JudgeUnavailableError, match="ConnectError"):
        client_with(httpx.MockTransport(refused)).complete_json("s", "u")


def test_api_key_never_appears_in_repr() -> None:
    assert KEY not in repr(Settings(_env_file=None, groq_api_key=KEY))


def test_judge_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.judge_provider == "none"
    assert judge_client_from_settings(settings) is None
    assert judge_from_settings(settings) is None


def test_groq_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("APP_GROQ_API_KEY", raising=False)
    monkeypatch.setenv("APP_JUDGE_PROVIDER", "groq")
    with pytest.raises(JudgeConfigurationError, match="APP_GROQ_API_KEY"):
        judge_client_from_settings(Settings(_env_file=None))


@pytest.mark.parametrize("variable", ["APP_GROQ_API_KEY", "GROQ_API_KEY"])
def test_groq_settings_from_environment(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    monkeypatch.setenv("APP_JUDGE_PROVIDER", "groq")
    monkeypatch.setenv(variable, KEY)
    monkeypatch.setenv("APP_GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("APP_JUDGE_SAMPLES", "3")
    settings = Settings(_env_file=None)

    client = judge_client_from_settings(settings)
    assert isinstance(client, GroqJudgeClient)
    assert client.model_id == "groq/openai/gpt-oss-120b"
    judge = judge_from_settings(settings)
    assert judge is not None
    assert judge.version == "llm-judge:groq/openai/gpt-oss-120b:prompt-1"
