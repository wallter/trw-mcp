"""The ``options`` argument of ``trw_recall``, ``trw_build_check`` and ``trw_review`` (PRD-CORE-291-FR03).

Each tool keeps its common arguments top-level and moves the rarely-set ones into
one ``options`` mapping, the pattern ``trw_learn``'s ``metadata`` established
(see ``_learn_arg_bags`` for why the wire type is a plain mapping and the
Pydantic model is applied inside the tool: a model parameter re-emits every field
into the schema and costs more than the flat parameters it replaced).

Keys are byte-identical to the flat parameter names they replaced, and an unknown
key raises a ToolError naming the accepted set: a dict parameter hides its field
names from the calling model, so a typo must fail loudly, never be dropped.
"""

from __future__ import annotations

from typing import TypeVar

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, ValidationError

from trw_mcp.tools._learn_arg_bags import _coerce_json, _first_validation_problem

__all__ = ["BuildCheckOptions", "RecallOptions", "ReviewOptions", "parse_options"]

_M = TypeVar("_M", bound=BaseModel)


class RecallOptions(BaseModel):
    """``trw_recall(options=...)``: shaping and filters most calls never set."""

    model_config = ConfigDict(extra="forbid")

    min_impact: float = 0.0
    topic: str | None = None
    include_tiers: list[str] | None = None
    as_of: str | None = None
    include_superseded: bool = False


class BuildCheckOptions(BaseModel):
    """``trw_build_check(options=...)``: detail beyond the pass/fail summary."""

    model_config = ConfigDict(extra="forbid")

    mypy_clean: bool = True
    failures: list[str] | None = None
    run_path: str | None = None
    min_coverage: float | None = None
    command_results: list[dict[str, object]] | str | None = None


class ReviewOptions(BaseModel):
    """``trw_review(options=...)``: run targeting and mode-specific inputs."""

    model_config = ConfigDict(extra="forbid")

    run_path: str | None = None
    prd_ids: list[str] | None = None
    external_receipt_path: str | None = None
    adversarial_pass: bool = False


def parse_options(model: type[_M], raw: dict[str, object] | str | None) -> _M:
    """Validate ``raw`` against ``model``; raise ToolError naming the accepted keys."""
    mapping, error = _coerce_json(raw)
    if mapping is None:
        raise ToolError(f"options {error}. Accepted keys: {sorted(model.model_fields)}")
    try:
        return model.model_validate(mapping)
    except ValidationError as exc:
        problem = _first_validation_problem(exc)
        raise ToolError(f"options {problem}. Accepted keys: {sorted(model.model_fields)}") from exc
