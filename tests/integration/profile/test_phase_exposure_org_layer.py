"""Sprint-97 adaptive-surface review F4 — org-layer policy reaches the middleware.

PROF-001 declares ``allowed_tools_by_phase`` as a single-source policy surface
resolved through the full layer chain (org / domain / task / session). Before
this fix, ``phase_exposure._resolve_policy`` resolved the profile WITHOUT any
session context, so ``resolve_session_profile`` only consulted the defaults +
client layers — an ``org.yaml`` ``allowed_tools_by_phase`` would silently never
reach the middleware's effective policy (FR-14 single-source claim partially
unhonored). This test proves an on-disk ``org.yaml`` override now flows into the
middleware's resolved policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML


def _write_org_layer(trw_dir: Path, allowed: dict[str, list[str]]) -> None:
    profiles_dir = trw_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with (profiles_dir / "org.yaml").open("w", encoding="utf-8") as fh:
        yaml.dump({"allowed_tools_by_phase": allowed}, fh)


def test_org_layer_allowlist_flows_into_middleware_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An org.yaml allowed_tools_by_phase reaches the middleware's effective policy."""
    from trw_mcp.middleware import phase_exposure

    trw_dir = tmp_path / ".trw"
    # A bespoke per-phase allowlist that no default seed would produce, so a
    # match proves the ORG layer (not the default policy) supplied it.
    org_allow = {"RESEARCH": ["trw_org_sentinel_tool", "trw_recall"]}
    _write_org_layer(trw_dir, org_allow)

    # Thread the test's trw_dir into the middleware's policy resolution and give
    # it a (sentinel) run dir so the session-resolve path runs.
    monkeypatch.setattr(phase_exposure, "_resolve_trw_dir_for_run", lambda run_dir: trw_dir)
    monkeypatch.setattr(
        phase_exposure,
        "resolve_run_dir_for_session",
        lambda **kwargs: tmp_path / "run",
    )

    policy = phase_exposure._resolve_policy(session_id="sess-1", fastmcp_context=None)

    # The org layer's RESEARCH allowlist is now the effective policy for RESEARCH.
    research_tools = policy.list_for("RESEARCH")
    assert "trw_org_sentinel_tool" in research_tools, (
        "org.yaml allowed_tools_by_phase must reach the middleware's effective policy"
    )
    assert "trw_recall" in research_tools


def test_policy_fails_open_when_resolution_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F4 fail-open contract: a resolution error falls back to the seed policy."""
    from trw_mcp.middleware import phase_exposure
    from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY

    def _boom(run_dir: object) -> Path:
        raise RuntimeError("trw_dir resolution exploded")

    monkeypatch.setattr(phase_exposure, "_resolve_trw_dir_for_run", _boom)

    policy = phase_exposure._resolve_policy(session_id="sess-1", fastmcp_context=None)
    assert policy is DEFAULT_PHASE_POLICY
