"""extend criterion types (Milestone 6 evaluation criteria)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19 21:00:00

Alembic autogenerate does not compare CHECK constraints, so this revision is hand-written.
It only widens the allowed values; no data changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = (
    "answer_contains",
    "answer_excludes",
    "answer_matches_regex",
    "declared_outcome",
    "tool_call_count",
    "citations_within",
)
_NEW = (
    *_OLD,
    "answer_exact",
    "required_facts",
    "forbidden_claims",
    "structured_output",
    "tool_calls_required",
    "prohibited_actions",
    "execution_limits",
    "expected_recovery",
    "rubric",
)
_NAME = "ck_criterion_results_criterion_type_values"


def _replace(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    # op.f(): the name is final; without it the naming convention prefixes it again.
    op.drop_constraint(op.f(_NAME), "criterion_results", type_="check")
    op.create_check_constraint(op.f(_NAME), "criterion_results", f"criterion_type IN ({allowed})")


def upgrade() -> None:
    _replace(_NEW)


def downgrade() -> None:
    # Fails (by design) if rows with the new criterion types exist.
    _replace(_OLD)
