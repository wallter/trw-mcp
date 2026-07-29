"""One gate for "should we install a trw-distill-dependent artifact?" (PRD-CORE-239).

`trw-distill` is a PROPRIETARY, licensed package. The framework must work
completely without it, and must exploit it fully when it is present. Before this
module existed, the install path had **no availability check at all** — a
repo-wide grep for `distill_installed` / `find_spec("trw_distill")` across
`bootstrap/` and `channels/` returned zero hits — so every client installer
planted distill-dependent artifacts into every project regardless of licence.

Concretely, an unlicensed user got `.claude/agents/trw-distill-explorer.md`
("powered by trw-distill"), plus instruction text telling them to run
`trw-distill self-improve risk-report`, which yields `command not found`. Under
`docs/CONSTITUTION.md`'s value hierarchy that is a Truthfulness failure, not a
cosmetic one.

**Gate, do not delete.** These artifacts are genuinely useful to a licensed
user, so the correct behaviour is to install them when the package is present
and skip them cleanly when it is not.

Delegates to `tools/_sidecar_substrate.check_tier_for_feature()` rather than
re-implementing the probe. That function is the runtime's single definition of
entitlement and grants on EITHER path: `trw_distill` importable, or a valid
expiry-bearing `.trw/entitlements.yaml` sentinel. Routing the install gate
through the same resolver is what keeps the two surfaces from disagreeing about
who is entitled — a review of the first version caught it checking only the
import, which would have refused the artifacts to a beta tester whose
entitlement is the sentinel while every runtime tool served them the sidecar.

It also inherits the suite's determinism: `conftest.py::_default_distill_absent`
pins `distill_installed`, which `check_tier_for_feature` consults first, so
bootstrap tests do not depend on whether the dev venv happens to have an
editable install.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

__all__ = ["distill_artifacts_entitled"]

#: The same feature id the runtime sidecar tools gate on, so the install
#: surface and the runtime surface can never disagree about who is entitled.
_TIER_FEATURE = "trw_before_edit_hint:distill_sidecar"


def distill_artifacts_entitled(*, artifact: str, repo_root: Path | None = None) -> bool:
    """Whether distill-dependent install artifacts should be written.

    Args:
        artifact: Short identifier for the artifact being gated, used only for
            the skip log line so an operator can see WHICH artifact was withheld
            and why. A silent skip would reproduce the defect this gate fixes,
            one level down.
        repo_root: Target repository, needed to find `.trw/entitlements.yaml`.
            Omitted only by callers with no repo in hand; the sentinel path is
            then unavailable and the install-proxy path decides alone.

    Returns:
        True when the project is entitled to the paid surface, else False.

    Delegates to `check_tier_for_feature`, which is the runtime's single
    definition of entitlement and grants on EITHER of two paths: `trw_distill`
    being importable, or a valid expiry-bearing `.trw/entitlements.yaml`
    sentinel. The first version of this gate checked only the import, which
    silently denied the explicit sentinel path — the one a beta tester on the
    proprietary programme actually has. That install would have been served the
    sidecar by every runtime tool while being refused the artifacts, satisfying
    "work without the package" while breaking "fully exploited with it".

    Fails CLOSED: `check_tier_for_feature` never raises, and any error reaching
    it is still treated as "not entitled". Withholding from a licensed user is a
    visible, recoverable annoyance; planting a paid-tool instruction in an
    unlicensed user's repo is a false statement that persists in their VCS.
    """
    try:
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        gate = check_tier_for_feature(repo_root, _TIER_FEATURE)
        entitled = bool(gate.allowed)
    except Exception:  # justified: probe failure must not break init-project
        log.warning(
            "distill_entitlement_probe_failed",
            artifact=artifact,
            outcome="treated_as_unentitled",
            exc_info=True,
        )
        return False

    if not entitled:
        log.info(
            "distill_artifact_skipped_unentitled",
            artifact=artifact,
            outcome="skipped",
            reason="no distill install and no entitlements.yaml sentinel",
        )
    return entitled
