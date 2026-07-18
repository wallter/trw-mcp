"""Tool-response-level beta-unlock coverage for the sidecar-consuming tools.

Complements ``test_sidecar_substrate.py`` (which pins the shared
``check_tier_for_feature`` gate in isolation): here we drive each tool's public
``compute_*`` entry point end-to-end and assert the tier gate is actually wired
into the response. The behavioral contract per tool:

- Free tier (no entitlement) → ``distill_status == "tier_required"``.
- Beta tier (tester-program bridge, sub_Y-f6QQ3Y_Os9b0vM) → the gate opens, so
  the status is anything BUT ``tier_required`` (typically a downstream
  sidecar/git status since these lean tests provide no valid sidecar).

The free-vs-beta pairing keeps each case non-vacuous: it proves the beta path
genuinely changed the outcome rather than the tool never gating at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools.before_edit_hint_batch import compute_before_edit_hint_batch
from trw_mcp.tools.codebase_risk_report import compute_codebase_risk_report
from trw_mcp.tools.cross_repo_ordering import compute_cross_repo_ordering
from trw_mcp.tools.entity_risk_map import compute_entity_risk_map
from trw_mcp.tools.ordering_compare import compute_ordering_compare


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier=tier, issued_to="t@t", expires_at=future)  # type: ignore[arg-type]
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
    )


# The four tools whose flow is repo_root -> tier gate, so a repo_root arg alone
# reaches the gate (no valid sidecar needed to observe tier_required vs unlocked).
_UNIFORM_TOOLS = {
    "before_edit_hint_batch": compute_before_edit_hint_batch,
    "codebase_risk_report": compute_codebase_risk_report,
    "entity_risk_map": compute_entity_risk_map,
    "ordering_compare": compute_ordering_compare,
}


class TestUniformSidecarToolsBetaUnlock:
    @pytest.mark.parametrize("tool_name", sorted(_UNIFORM_TOOLS))
    def test_free_tier_is_gated(self, tmp_path: Path, tool_name: str) -> None:
        compute = _UNIFORM_TOOLS[tool_name]
        result = compute(repo_root=str(tmp_path))
        assert result.distill_status == "tier_required", tool_name

    @pytest.mark.parametrize("tool_name", sorted(_UNIFORM_TOOLS))
    def test_beta_tier_opens_the_gate(self, tmp_path: Path, tool_name: str) -> None:
        _write_entitlement(tmp_path / ".trw", "beta")
        compute = _UNIFORM_TOOLS[tool_name]
        result = compute(repo_root=str(tmp_path))
        # Gate opened — the tester-program (beta) user is not shown the
        # paid-tier remediation. Downstream status (no_git_sha / sidecar_missing)
        # is fine; it just must not be tier_required.
        assert result.tier == "beta", tool_name
        assert result.distill_status != "tier_required", tool_name


class TestCrossRepoOrderingBetaUnlock:
    """cross_repo_ordering checks the sidecar BEFORE the tier gate on the
    no-sidecar path, so we hand it an existing (junk) sidecar_path to reach the
    gate for both the free and beta cases."""

    def _junk_sidecar(self, tmp_path: Path) -> str:
        sidecar = tmp_path / "cross-repo-aggregate-x.json"
        sidecar.write_text("{}")
        return str(sidecar)

    def test_free_tier_is_gated(self, tmp_path: Path) -> None:
        result = compute_cross_repo_ordering(
            repo_root=str(tmp_path),
            sidecar_path=self._junk_sidecar(tmp_path),
        )
        assert result.distill_status == "tier_required"

    def test_beta_tier_opens_the_gate(self, tmp_path: Path) -> None:
        _write_entitlement(tmp_path / ".trw", "beta")
        result = compute_cross_repo_ordering(
            repo_root=str(tmp_path),
            sidecar_path=self._junk_sidecar(tmp_path),
        )
        assert result.tier == "beta"
        assert result.distill_status != "tier_required"
