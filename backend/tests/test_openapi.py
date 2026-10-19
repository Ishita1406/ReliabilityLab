"""The committed OpenAPI schema (source of the frontend's API types) matches the app."""

from app.api.openapi_export import OUTPUT, render


def test_committed_openapi_schema_is_current() -> None:
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        "run `uv run python -m app.api.openapi_export` and regenerate frontend types"
    )
