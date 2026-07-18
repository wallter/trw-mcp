"""Client integration appendix — transport-loss + capability instructions.

Belongs to the ``_client_integrations.py`` facade family. Composes the two
client-visible FR06 renderers into ONE appendix block that every supported
client's generated ``AGENTS.md`` instructions carry:

- PRD-CORE-215-FR06 — ``client_transport_guidance`` (marker ``trw:transport-loss``):
  the four client-observed transport-loss boundaries and their safe recoveries.
- PRD-CORE-218-FR06 — ``render_client_capability_instructions`` (marker
  ``trw:capabilities``): the three-class (available / discoverable / gated)
  listing derived from the LIVE surface manifest seam.

Extracted as its own sibling because ``_client_integrations.py`` sits at the
350 effective-LOC ceiling; the renderers stay there, the composition lives here.
The capability projection is checked for parity against the resolved profile
before it is emitted, so a rendering/manifest regression drops the block loudly
(structured warning + surfaced ``parity_failures``) rather than shipping a
drifted capability claim.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from trw_mcp.bootstrap._client_integrations import (
    ProjectionFormat,
    ProjectionParityFailure,
    ResolvedProfile,
    check_projection_parity,
    client_transport_guidance,
    render_capability_projection,
    render_client_capability_instructions,
    resolved_profile_from_manifest_seam,
)

logger = structlog.get_logger(__name__)

#: Default task type used to resolve the capability surface for generated
#: instruction files. ``coding`` is the broadest standard profile (kernel +
#: verification + code_navigation); the marker stays truthful for other tasks
#: because it names the resolution it derives from.
_DEFAULT_TASK_TYPE = "coding"


@dataclass(frozen=True, slots=True)
class ClientIntegrationAppendix:
    """Rendered appendix text plus any capability-parity failures detected.

    ``text`` is what gets injected into the generated instruction file. When
    ``parity_failures`` is non-empty the capability block is intentionally
    omitted from ``text`` (fail-loud) so no drifted capability claim ships.
    """

    text: str
    parity_failures: tuple[ProjectionParityFailure, ...]


def _resolve_profile(task_type: str) -> ResolvedProfile:
    """Resolve the live capability profile, or an empty profile as a floor.

    ``resolved_profile_from_manifest_seam`` returns ``None`` only if the FR01
    surface manifest is absent; an empty profile still renders the three-class
    scaffold so the ``trw:capabilities`` marker and labels are never missing.
    """
    profile = resolved_profile_from_manifest_seam(task_type)
    if profile is None:
        return ResolvedProfile(task_type=task_type, capabilities=())
    return profile


def build_client_integration_appendix(
    client_id: str, *, task_type: str = _DEFAULT_TASK_TYPE
) -> ClientIntegrationAppendix:
    """Build the transport-loss + capability appendix for one client surface.

    The capability projection is rendered from the resolved profile and then
    parity-checked against that same profile. On drift the capability block is
    dropped and a structured ``client_capability_parity_drift`` warning is
    logged; the transport-loss block always renders (it is manifest-independent).
    """
    profile = _resolve_profile(task_type)
    projection = render_capability_projection(profile, client_id=client_id, fmt=ProjectionFormat.BULLET_LIST)
    failures = check_projection_parity(profile, projection)

    parts = [client_transport_guidance(client_id)]
    if failures:
        logger.warning(
            "client_capability_parity_drift",
            client_id=client_id,
            task_type=task_type,
            failures=[f.detail for f in failures],
        )
    else:
        parts.append(render_client_capability_instructions(profile, client_id=client_id))

    return ClientIntegrationAppendix(text="\n\n".join(parts), parity_failures=failures)


def render_client_integration_appendix(client_id: str, *, task_type: str = _DEFAULT_TASK_TYPE) -> str:
    """Return only the appendix text (transport-loss + capability blocks)."""
    return build_client_integration_appendix(client_id, task_type=task_type).text
