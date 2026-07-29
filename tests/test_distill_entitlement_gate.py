"""PRD-CORE-239 — an unlicensed project must not be handed paid-tool artifacts.

`trw-distill` is a PROPRIETARY, licensed package. Before this gate existed the
install path had **no availability check anywhere** — a repo-wide grep for
`distill_installed` / `find_spec("trw_distill")` across `bootstrap/` and
`channels/` returned zero hits. Every client installer therefore planted
distill-dependent artifacts into every project, licensed or not:

- `.github/instructions/trw-distill-hotspots.instructions.md` containing
  ``run `trw-distill self-improve risk-report` `` — a command an unlicensed user
  cannot run.
- `.cursor/rules/distill-*.mdc` whose description read *"TRW distill data
  available — quota exceeded"*, which is false twice over for a stub: no data
  exists and no quota was hit.
- `.claude/agents/trw-distill-explorer.md`, `.antigravitycli/agents/…`, and
  `.opencode/agents/…` — three copies of an agent that cannot function without
  the package.

The structural test at the bottom is the one that matters most: it fails if any
future edit reintroduces an ungated `trw-distill` command into the install path,
which is how this defect arrived in the first place.
"""

from __future__ import annotations

import pytest


def test_gate_is_closed_when_distill_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default for an unlicensed project is: do not install."""
    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)

    assert distill_artifacts_entitled(artifact="test-artifact") is False


def test_gate_is_open_when_distill_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A licensed project still gets the full surface.

    The operator requirement has two halves — work without the package, and be
    fully exploited with it. A gate that never opens would satisfy only one.
    """
    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)

    assert distill_artifacts_entitled(artifact="test-artifact") is True


def test_gate_fails_closed_when_the_probe_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken probe withholds the artifact rather than installing it.

    Direction matters: withholding from a licensed user is a visible,
    recoverable annoyance; planting a paid-tool instruction in an unlicensed
    user's repo is a false statement that persists in their version control.
    """
    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    def _boom() -> bool:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", _boom)

    assert distill_artifacts_entitled(artifact="test-artifact") is False


def test_skip_is_logged_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent skip would reproduce this defect one level down.

    An operator who wonders why the explorer agent is missing must be able to
    find the reason; "nothing happened and nothing was said" is the shape the
    whole PRD exists to remove.
    """
    import structlog
    from structlog.testing import capture_logs

    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)

    with capture_logs() as logs:
        distill_artifacts_entitled(artifact="cc-05-distill-explorer")

    skips = [e for e in logs if e.get("event") == "distill_artifact_skipped_unentitled"]
    assert skips, f"the skip must be logged; got {[e.get('event') for e in logs]}"
    assert skips[0]["artifact"] == "cc-05-distill-explorer", (
        "the log must name WHICH artifact was withheld, or an operator cannot act on it"
    )
    del structlog  # imported for the fixture's side effect only


# --- The structural guard --------------------------------------------------


def test_no_ungated_trw_distill_command_in_the_install_path() -> None:
    """FR02: no installer may write a `trw-distill` command without a gate.

    This is the regression guard for the original defect, and it is deliberately
    structural rather than a list of known-bad files: the defect was not one bad
    string, it was an ENTIRE SUBSYSTEM with no availability check. A test naming
    the four files I happened to fix would pass while a fifth was added.

    A module is a finding when it INSTRUCTS the user to run trw-distill and
    does not reference the entitlement gate. Comments are excluded, because the
    fixes themselves describe the removed text.

    The predicate is imperative context, not a bare mention. A first version
    matched any occurrence of `trw-distill ` and immediately produced a false
    positive on `"description": "TRW managed: trw-distill PostToolUse
    telemetry"` — a label for the distill-FREE telemetry hook, which must be
    preserved. Naming a package is not the defect; telling an unlicensed user to
    execute it is. Three imperative markers, kept deliberately small so this
    stays a structural check rather than a vocabulary list.
    """
    import re
    from pathlib import Path

    import trw_mcp

    root = Path(trw_mcp.__file__).parent
    invocation = re.compile(r"(?:run|regenerate)\W{0,4}trw-distill\s+\w", re.IGNORECASE)
    offenders: list[str] = []

    for path in sorted((root / "bootstrap").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        if not invocation.search(code):
            continue
        if "distill_artifacts_entitled" in code or "distill_installed" in code:
            continue
        offenders.append(path.name)

    assert not offenders, (
        "these installers emit a `trw-distill` command with no licence gate, so "
        "an unlicensed project would be told to run a tool it does not have: "
        f"{offenders}"
    )


def test_the_entitlements_sentinel_also_opens_the_gate(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A licensed beta tester has a sentinel, not necessarily an install.

    The runtime's entitlement resolver (`check_tier_for_feature`) grants on
    EITHER path: `trw_distill` importable, or a valid expiry-bearing
    `.trw/entitlements.yaml`. The first version of this gate checked only the
    import, so a tester on the proprietary programme was served the sidecar by
    every runtime tool while being refused the install artifacts — satisfying
    "works without the package" and breaking "fully exploited with it". Found
    in review, and this is the case that pins it.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled
    from trw_mcp.state._entitlements import sign_entitlement_for_dev

    repo_root = Path(str(tmp_path))
    trw_dir = repo_root / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    expires = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier="beta", issued_to="t@t", expires_at=expires)
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: beta\nissued_to: t@t\nexpires_at: '{expires}'\nsignature: {sig}\n",
        encoding="utf-8",
    )

    # The package is absent — the sentinel must carry the entitlement alone.
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)

    assert distill_artifacts_entitled(artifact="cc-05", repo_root=repo_root) is True


def test_an_unlicensed_repo_with_no_sentinel_is_still_refused(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paired direction: broadening the gate must not open it to everyone."""
    from pathlib import Path

    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    repo_root = Path(str(tmp_path))
    (repo_root / ".trw").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)

    assert distill_artifacts_entitled(artifact="cc-05", repo_root=repo_root) is False
