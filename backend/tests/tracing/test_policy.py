"""Redaction and payload bounds. All "secrets" below are synthetic placeholders."""

import json

import pytest
from pydantic import JsonValue

from app.tracing.policy import REDACTED, TracePayloadPolicy, is_sensitive_key, sanitize

FAKE_API_KEY = "sk-test-0000000000000000FAKE"
FAKE_GITHUB = "ghp_" + "A" * 36
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "api_key",
        "X-Api-Key",
        "Authorization",
        "accessToken",
        "refresh_token",
        "client_secret",
        "privateKey",
        "session_id",
        "Cookie",
        "aws_access_key_id",
        "credentials",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "query",
        "document_id",
        "author",
        "input_tokens",
        "output_tokens",
        "max_tokens",
        "limit",
        "title",
    ],
)
def test_ordinary_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_values_under_sensitive_keys_are_redacted_recursively() -> None:
    payload: JsonValue = {
        "query": "lighthouse",
        "auth": {"password": "hunter2", "nested": [{"api_key": "abc"}]},
        "input_tokens": 12,
        "token": None,
    }

    result = sanitize(payload)

    assert result.value == {
        "query": "lighthouse",
        "auth": {"password": REDACTED, "nested": [{"api_key": REDACTED}]},
        "input_tokens": 12,
        "token": None,  # nothing to hide
    }
    assert result.redactions == 2
    assert not result.truncated


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"use key {FAKE_API_KEY} please", FAKE_API_KEY),
        ("Authorization: Bearer abcdefghijklmnop0123456789", "abcdefghijklmnop0123456789"),
        (f"token={FAKE_GITHUB}", FAKE_GITHUB),
        (f"id {FAKE_AWS}", FAKE_AWS),
        ("xoxb-1234567890-FAKE", "xoxb-1234567890-FAKE"),
        ("postgresql://user:s3cretpw@db:5432/x", "s3cretpw"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIFAKE\n-----END RSA PRIVATE KEY-----", "MIIFAKE"),
    ],
)
def test_secret_looking_substrings_are_redacted(text: str, secret: str) -> None:
    result = sanitize({"message": text, "list": [text]})

    serialised = json.dumps(result.value)
    assert secret not in serialised
    assert REDACTED in serialised
    assert result.redactions == 2


def test_url_redaction_keeps_user_and_host() -> None:
    value = sanitize("postgresql://user:s3cretpw@db:5432/x").value

    assert value == f"postgresql://user:{REDACTED}@db:5432/x"


def test_long_strings_lists_and_dicts_are_cut_with_markers() -> None:
    policy = TracePayloadPolicy(max_string_chars=40, max_items=3, max_depth=8)
    payload: JsonValue = {
        "text": "x" * 100,
        "items": list(range(10)),
        "wide": {f"k{i}": i for i in range(5)},
    }

    result = sanitize(payload, policy)

    assert result.truncated
    value = result.value
    assert isinstance(value, dict)
    assert value["text"] == "x" * 40 + "…[truncated 60 chars]"
    assert value["items"] == [0, 1, 2, "…[truncated 7 items]"]
    assert value["wide"] == {"k0": 0, "k1": 1, "k2": 2, "…": "[truncated 2 keys]"}


def test_depth_is_bounded() -> None:
    nested: JsonValue = {"a": {"b": {"c": {"d": 1}}}}

    result = sanitize(nested, TracePayloadPolicy(max_depth=2))

    assert result.value == {"a": {"b": "…[truncated: max depth]"}}
    assert result.truncated


def test_oversized_payload_becomes_a_bounded_preview() -> None:
    policy = TracePayloadPolicy(max_payload_bytes=1_024, max_string_chars=10_000, max_items=1_000)
    payload: JsonValue = {"rows": ["é" * 50 for _ in range(200)]}

    result = sanitize(payload, policy)

    assert result.truncated
    assert isinstance(result.value, str)
    assert len(json.dumps(result.value, ensure_ascii=False).encode()) <= policy.max_payload_bytes
    assert result.value.endswith("…[truncated: payload exceeded 1024 bytes]")
    assert result.original_bytes > policy.max_payload_bytes


def test_small_payloads_pass_through_unchanged() -> None:
    payload: JsonValue = {"results": [{"document_id": "a", "score": 1.0}], "n": None}

    result = sanitize(payload)

    assert result.value == payload
    assert (result.truncated, result.redactions) == (False, 0)
    assert result.original_bytes == len(json.dumps(payload).encode())


def test_input_is_not_mutated() -> None:
    payload: dict[str, JsonValue] = {"password": "x", "text": "y" * 10_000}
    snapshot = json.dumps(payload)

    sanitize(payload)

    assert json.dumps(payload) == snapshot
