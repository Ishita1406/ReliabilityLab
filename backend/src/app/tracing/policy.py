"""What may be stored in a trace, and how much of it.

Every JSON payload and free-text field that the collector stores passes through ``sanitize``:

1. **Redaction.** Values under secret-looking keys (``password``, ``api_key``, ``authorization``,
   ``*token``, ...) are replaced with ``[REDACTED]``, as are secret-looking substrings anywhere
   in strings (``sk-...`` API keys, ``Bearer ...`` tokens, AWS access key IDs, GitHub and Slack
   tokens, PEM private keys, and passwords embedded in URLs).
2. **Structural bounds.** Strings longer than ``max_string_chars``, lists or objects with more
   than ``max_items`` entries, and nesting deeper than ``max_depth`` are cut, leaving an explicit
   marker so the cut is visible.
3. **Size bound.** If the bounded value still serialises to more than ``max_payload_bytes``,
   it is replaced by a string preview of that size.

``SanitizedPayload`` reports whether anything was truncated, how many values were redacted, and
the serialised size of the (redacted) original, so stored traces say what they are missing.
Redaction is best-effort pattern matching: it reduces, but cannot eliminate, the risk of storing
a secret that does not look like one.
"""

import json
import re
from dataclasses import dataclass

from pydantic import Field, JsonValue

from app.domain.base import DomainModel

REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "apikey",
    "authorization",
    "credential",
    "cookie",
    "privatekey",
    "accesskey",
    "sessionid",
)

_SECRET_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
        REDACTED,
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), REDACTED),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{16,}=*"), f"Bearer {REDACTED}"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):[^/\s@]+@"), rf"\1:{REDACTED}@"),
)


class TracePayloadPolicy(DomainModel):
    max_payload_bytes: int = Field(default=16_384, ge=256)
    max_string_chars: int = Field(default=4_000, ge=32)
    max_items: int = Field(default=100, ge=1)
    max_depth: int = Field(default=8, ge=1)


DEFAULT_POLICY = TracePayloadPolicy()


@dataclass(frozen=True)
class SanitizedPayload:
    value: JsonValue
    truncated: bool
    redactions: int
    original_bytes: int


def is_sensitive_key(key: str) -> bool:
    normalised = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalised.endswith("token") or any(part in normalised for part in _SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> tuple[str, int]:
    count = 0
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


class _Sanitizer:
    def __init__(self, policy: TracePayloadPolicy) -> None:
        self.policy = policy
        self.redactions = 0
        self.truncated = False

    def redact(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            text, n = redact_text(value)
            self.redactions += n
            return text
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            result: dict[str, JsonValue] = {}
            for key, item in value.items():
                if is_sensitive_key(key) and item is not None:
                    self.redactions += 1
                    result[key] = REDACTED
                else:
                    result[key] = self.redact(item)
            return result
        return value

    def bound(self, value: JsonValue, depth: int = 0) -> JsonValue:
        policy = self.policy
        if isinstance(value, str):
            if len(value) > policy.max_string_chars:
                self.truncated = True
                omitted = len(value) - policy.max_string_chars
                return f"{value[: policy.max_string_chars]}…[truncated {omitted} chars]"
            return value
        if isinstance(value, list | dict) and depth >= policy.max_depth:
            self.truncated = True
            return "…[truncated: max depth]"
        if isinstance(value, list):
            items = [self.bound(item, depth + 1) for item in value[: policy.max_items]]
            if len(value) > policy.max_items:
                self.truncated = True
                items.append(f"…[truncated {len(value) - policy.max_items} items]")
            return items
        if isinstance(value, dict):
            entries = list(value.items())
            bounded: dict[str, JsonValue] = {
                key: self.bound(item, depth + 1) for key, item in entries[: policy.max_items]
            }
            if len(entries) > policy.max_items:
                self.truncated = True
                bounded["…"] = f"[truncated {len(entries) - policy.max_items} keys]"
            return bounded
        return value


def _serialised_size(value: JsonValue) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def sanitize(value: JsonValue, policy: TracePayloadPolicy = DEFAULT_POLICY) -> SanitizedPayload:
    """Redact secrets, then bound the payload's structure and serialised size."""
    sanitizer = _Sanitizer(policy)
    redacted = sanitizer.redact(value)
    original_bytes = _serialised_size(redacted)
    bounded = sanitizer.bound(redacted)
    if _serialised_size(bounded) > policy.max_payload_bytes:
        sanitizer.truncated = True
        text = json.dumps(bounded, ensure_ascii=False)
        # Leave room for the marker; slicing characters keeps the result valid UTF-8.
        budget = policy.max_payload_bytes // 4  # worst case 4 bytes per character
        bounded = f"{text[:budget]}…[truncated: payload exceeded {policy.max_payload_bytes} bytes]"
    return SanitizedPayload(
        value=bounded,
        truncated=sanitizer.truncated,
        redactions=sanitizer.redactions,
        original_bytes=original_bytes,
    )
