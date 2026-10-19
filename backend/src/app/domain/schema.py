"""Machine-readable JSON Schema contract for benchmark datasets.

The schemas are generated from the Pydantic models and committed under ``backend/schemas/``
so non-Python tools (editors, CI, other services) can validate dataset files. Regenerate with::

    uv run python -m app.domain.schema

and verify the committed files are current with ``--check`` (exit code 1 on drift).

JSON Schema expresses structure, types, enums, and patterns. Cross-field rules (unique IDs,
scenario-to-case references, one baseline per case, score consistency) are enforced only by
the Pydantic validators.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.domain.benchmark import BenchmarkCase, BenchmarkSuite

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "schemas"

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "benchmark-case.schema.json": BenchmarkCase,
    "benchmark-suite.schema.json": BenchmarkSuite,
}


def build_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {"$schema": JSON_SCHEMA_DIALECT, **model.model_json_schema()}


def render_schema(model: type[BaseModel]) -> str:
    return json.dumps(build_schema(model), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--check", action="store_true", help="Fail if committed schemas are out of date."
    )
    args = parser.parse_args(argv)

    stale: list[str] = []
    for filename, model in SCHEMA_MODELS.items():
        path: Path = args.output_dir / filename
        rendered = render_schema(model)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path}")

    if stale:
        print("out-of-date schemas (run `uv run python -m app.domain.schema`):", file=sys.stderr)
        for path_str in stale:
            print(f"  {path_str}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
