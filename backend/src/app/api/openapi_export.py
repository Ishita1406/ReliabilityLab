"""Export the API's OpenAPI schema for the frontend's generated TypeScript types.

uv run python -m app.api.openapi_export          # write backend/schemas/openapi.json
uv run python -m app.api.openapi_export --check  # exit 1 if the committed file is stale
"""

import argparse
import json
import sys
from pathlib import Path

from app.main import create_app

OUTPUT = Path(__file__).resolve().parents[3] / "schemas" / "openapi.json"


def render() -> str:
    schema = create_app().openapi()
    schema["info"]["title"] = "AI Reliability Lab API"  # stable, independent of APP_SERVICE_NAME
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    rendered = render()
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} is out of date; run `uv run python -m app.api.openapi_export`",
                file=sys.stderr,
            )
            return 1
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
