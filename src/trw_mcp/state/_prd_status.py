"""A PRD's own lifecycle status, immune to an embedded status-header mapping (PRD-STATUS-HEADER-SHADOW).

Shared by the INDEX/ROADMAP projection (``index_sync``) and the requirements registry, which both
bucket PRDs by status and both read it through :func:`trw_mcp.state.prd_utils.parse_frontmatter`.
"""

from __future__ import annotations

import re

_SCALAR_STATUS_RE = re.compile(r"^\s*status:\s*['\"]?([A-Za-z_-]+)['\"]?\s*$", re.MULTILINE)


def prd_status(fm: dict[str, object], content: str) -> str:
    """The PRD's own lifecycle status, lower-cased (PRD-STATUS-HEADER-SHADOW).

    :func:`parse_frontmatter` flattens ``prd:`` with top-level keys winning, so an embedded
    auto-generated "PRD Status Header" (a top-level ``status:`` MAPPING of implementation
    fields) replaces ``prd.status``. A non-scalar status falls back to the first scalar
    ``status:`` line of *content*, which is ``prd.status`` in the AARE-F layout.
    """
    status = fm.get("status", "draft")
    if isinstance(status, dict | list):
        found = _SCALAR_STATUS_RE.search(content)
        status = found.group(1) if found else "draft"
    return str(status).strip().lower()
