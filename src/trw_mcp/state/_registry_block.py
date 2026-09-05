"""Executable-registry projection block (PRD-QUAL-121-FR03, PRD-CORE-244-FR07).

Belongs to the ``index_sync.py`` facade, which re-exports this as
``_render_registry_block`` and renders it into both the INDEX and the ROADMAP
catalogue. Extracted so the expiry-reporting branch fits: ``index_sync`` sat at
exactly the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.state.requirements_registry import RegistryBuildResult

__all__ = ["render_registry_block"]


def render_registry_block(registry: RegistryBuildResult | None) -> list[str]:
    """Render the executable-registry section (PRD-QUAL-121-FR03).

    This block is derivable ONLY from the registry (scheduling ledger + epoch +
    receipt digest) — never from frontmatter — so the frontmatter scan alone can
    no longer reproduce the projection bytes. Bullet lists are used instead of
    tables so ``check_prd_ids`` catalogue-row parsing never mistakes execution
    state for a PRD title.

    PRD-CORE-244-FR07: when the registry's expiry was never evaluated, the block
    SAYS SO. Before this, the unevaluated case rendered as the silent omission of
    the expired line — visually identical to "nothing has expired" — beneath a
    "hot path: 350 of 350 executable" produced by an evaluator that had never
    run, while 261 of those 350 entries carried a past-dated renewal date.
    """
    if registry is None or registry.epoch is None:
        return []
    lines = [
        f"### Executable Registry (epoch {registry.epoch.sequence} @ {registry.epoch.effective_utc_date})",
        "",
        f"- registry receipt: `{registry.receipt_digest()}`",
        f"- scheduling ledger head: `{registry.head_digest[:16]}`",
        f"- hot path: {len(registry.hot_path)} of {len(registry.entries)} executable",
    ]
    lines.extend(
        f"- {str(entry.execution_state).upper()}: {entry.prd_id} ({entry.owner})"
        for entry in registry.entries
        if str(entry.execution_state) in ("active", "blocked_external")
    )
    if not registry.expiry_evaluated:
        lines.append("- expiry: not evaluated (no authorized evaluation epoch)")
    elif registry.expired:
        lines.append("- expired (left hot path): " + ", ".join(registry.expired))
    lines.append("")
    return lines
