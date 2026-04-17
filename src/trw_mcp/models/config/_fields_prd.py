"""PRD-authoring convention fields for TRWConfig.

Ships with a minimal generic set of PRD categories (CORE/QUAL/INFRA/LOCAL/
EXPLR/RESEARCH/FIX). Any project using trw-mcp may extend the accepted
category set by listing additional names in `.trw/config.yaml`:

    extra_prd_categories:
      - EVAL
      - HPO
      - INTENT
      - SCALE

The union of built-in + extra categories is what `trw_prd_create` and
`trw_prd_validate` accept.

See also: `trw_mcp.state.validation.prd_integrity.allowed_prd_categories()`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class _PRDFields(BaseSettings):
    """Configuration fields governing PRD authoring + validation."""

    extra_prd_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Project-specific PRD category names accepted in addition to the "
            "built-in generic set (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, "
            "FIX). Case-insensitive; compared upper-case at validation time. "
            "Example (TRW monorepo): "
            "['EVAL', 'HPO', 'INTENT', 'SCALE', 'THRASH', 'SEC', 'DIST']."
        ),
    )
