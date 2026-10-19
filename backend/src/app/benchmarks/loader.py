"""Load and validate benchmark suite dataset files.

Bundled datasets live at ``datasets/<suite-id>/<version>.json``. The path is part of the
contract: a file's directory must match the suite ``id`` and its name the suite ``version``,
so a suite version can never be silently overwritten by editing another file.
"""

from pathlib import Path

from pydantic import ValidationError

from app.domain.benchmark import BenchmarkSuite

DATASETS_DIR = Path(__file__).parent / "datasets"


class DatasetError(Exception):
    """A dataset file is missing, invalid, or stored under the wrong path."""


def load_suite(path: Path) -> BenchmarkSuite:
    """Parse and validate a suite file, checking its path matches its id and version."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DatasetError(f"cannot read dataset {path}: {exc}") from exc
    try:
        suite = BenchmarkSuite.model_validate_json(raw)
    except ValidationError as exc:
        raise DatasetError(f"invalid dataset {path}:\n{exc}") from exc

    expected = Path(suite.id) / f"{suite.version}.json"
    if path.parts[-2:] != expected.parts:
        raise DatasetError(
            f"dataset {path} declares id '{suite.id}' version '{suite.version}' "
            f"and must be stored as .../{expected}"
        )
    return suite


def bundled_suite_path(suite_id: str, version: str) -> Path:
    return DATASETS_DIR / suite_id / f"{version}.json"


def load_bundled_suite(suite_id: str, version: str) -> BenchmarkSuite:
    return load_suite(bundled_suite_path(suite_id, version))
