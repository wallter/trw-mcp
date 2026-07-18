"""Registry-driven dispatch for client-specific bootstrap/update integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

InstallFn = Callable[[Path, bool, dict[str, list[str]], list[str] | None], None]
UpdateFn = Callable[[Path, dict[str, list[str]], str | None, dict[str, str] | None], None]


@dataclass(frozen=True, slots=True)
class ClientIntegration:
    """Client-specific bootstrap/update integration binding."""

    name: str
    platform_ids: tuple[str, ...]
    install: InstallFn
    update: UpdateFn

    def matches(self, ide_targets: Iterable[str]) -> bool:
        target_set = set(ide_targets)
        return any(platform_id in target_set for platform_id in self.platform_ids)


def _install_opencode(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_opencode_artifacts

    _install_opencode_artifacts(target_dir, force=force, result=result)


def _update_opencode(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._update_project import _update_opencode_artifacts

    _update_opencode_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_cursor(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    ide_targets: list[str] | None,
) -> None:
    from ._init_project import _install_cursor_artifacts

    _install_cursor_artifacts(target_dir, force=force, result=result, ide_targets=ide_targets)


def _update_cursor(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    _: dict[str, str] | None,
) -> None:
    from ._update_project import _update_cursor_artifacts

    _update_cursor_artifacts(target_dir, result, ide_override=ide_override)


def _install_codex(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_codex_artifacts

    _install_codex_artifacts(target_dir, force=force, result=result)


def _update_codex(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._update_project import _update_codex_artifacts

    _update_codex_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_copilot(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_copilot_artifacts

    _install_copilot_artifacts(target_dir, force=force, result=result)


def _update_copilot(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._update_project import _update_copilot_artifacts

    _update_copilot_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_antigravity(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_antigravity_artifacts

    _install_antigravity_artifacts(target_dir, force=force, result=result)


def _update_antigravity(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._update_project import _update_antigravity_artifacts

    _update_antigravity_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


CLIENT_INTEGRATIONS: tuple[ClientIntegration, ...] = (
    ClientIntegration("opencode", ("opencode",), _install_opencode, _update_opencode),
    ClientIntegration("cursor", ("cursor-ide", "cursor-cli"), _install_cursor, _update_cursor),
    ClientIntegration("codex", ("codex",), _install_codex, _update_codex),
    ClientIntegration("copilot", ("copilot",), _install_copilot, _update_copilot),
    ClientIntegration("antigravity-cli", ("antigravity-cli",), _install_antigravity, _update_antigravity),
)


# ---------------------------------------------------------------------------
# Canonical client-ID drift guard (FIX C — client-adapter parity audit)
#
# ``SUPPORTED_IDES`` (bootstrap/_utils.py) is the single source of truth for the
# set of installable client IDs. ``CLIENT_INTEGRATIONS`` above binds a subset of
# those IDs to per-client bootstrap/update dispatchers. One supported ID is
# INTENTIONALLY not dispatched here and is encoded as an explicit, documented
# exclusion set rather than left as a silent gap:
#
#   - ``claude-code`` — the framework-core init path writes ``.claude/*``
#     directly; there is no separate per-client integration to register.
#
# A drift test asserts every ``SUPPORTED_IDES``
# entry is either covered by a ``platform_ids`` binding or in this set, so a
# newly-added client can never silently no-op.
#
# ``gemini`` and ``aider`` were retired 2026-07-11 (Gemini CLI deprecated by
# Google; aider never had an adapter). They are no longer in ``SUPPORTED_IDES``,
# so they are neither dispatched nor excluded here.
# ---------------------------------------------------------------------------
_INTEGRATION_EXCLUDED_IDES: frozenset[str] = frozenset({"claude-code"})


def iter_matching_integrations(ide_targets: Iterable[str]) -> tuple[ClientIntegration, ...]:
    """Return integrations activated by the provided target platform IDs."""
    return tuple(integration for integration in CLIENT_INTEGRATIONS if integration.matches(ide_targets))


def run_install_integrations(
    target_dir: Path,
    ide_targets: list[str],
    *,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Run matching bootstrap installers in stable registry order.

    Each per-IDE installer is isolated: a raised exception is logged and
    appended to ``result['errors']`` so the remaining IDEs are still installed.
    Without this, one installer's traceback would abort the whole init and
    escape the documented dict-contract return.
    """
    for integration in iter_matching_integrations(ide_targets):
        try:
            integration.install(target_dir, force, result, ide_targets)
        except Exception as exc:  # justified: per-IDE isolation, continue with remaining IDEs
            logger.exception("client_install_failed", client=integration.name)
            result.setdefault("errors", []).append(f"{integration.name} install failed: {type(exc).__name__}: {exc}")


def run_update_integrations(
    target_dir: Path,
    ide_targets: list[str],
    *,
    ide_override: str | None,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None,
) -> None:
    """Run matching update dispatchers in stable registry order.

    Update adapters have the same failure-isolation contract as installers: a
    broken client integration is reported without preventing later matching
    integrations from refreshing their artifacts.
    """
    for integration in iter_matching_integrations(ide_targets):
        try:
            integration.update(target_dir, result, ide_override, manifest_hashes)
        except Exception as exc:  # justified: per-client isolation, continue with remaining clients
            logger.exception("client_update_failed", client=integration.name)
            result.setdefault("errors", []).append(f"{integration.name} update failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Client-visible transport-loss retry protocol (PRD-CORE-215-FR06)
#
# stdio transport can drop bytes without either side agreeing on whether the
# operation committed. The four boundaries below are all *client-observed*: the
# server never claims to know whether the client received the response bytes.
# Each boundary maps to exactly one safe recovery action — reuse the request
# identity for an idempotent exact retry, or query the owner status locator —
# and records whether operation acceptance stays ``uncertain``. The rendered
# guidance is embedded in every supported client's generated integration
# snippet so no client is left without the protocol.
# ---------------------------------------------------------------------------


class TransportLossSafeAction(str, Enum):
    """The two safe recovery actions after client-observed transport loss."""

    REUSE_REQUEST_IDENTITY = "reuse_request_identity"
    QUERY_OWNER_STATUS = "query_owner_status"


@dataclass(frozen=True, slots=True)
class TransportLossBoundary:
    """One client-observed transport-loss boundary and its safe recovery.

    ``observed_by`` is always the client — the server emits fingerprint,
    request identity, owner-status locator, and retry-safety fields, but never
    asserts it observed whether the client received bytes. ``safe_action`` is
    one of the two idempotent recoveries; ``uncertainty_preserved`` is True
    when operation acceptance remains unknowable and the client must keep an
    ``uncertain`` outcome rather than inventing success or failure.
    """

    key: str
    title: str
    observed_by: str
    classification: str
    safe_action: TransportLossSafeAction
    safe_action_text: str
    uncertainty_preserved: bool


TRANSPORT_LOSS_PROTOCOL: tuple[TransportLossBoundary, ...] = (
    TransportLossBoundary(
        key="loss_before_known_acceptance",
        title="Connection lost before the server acknowledged acceptance",
        observed_by="client",
        classification="acceptance unknowable — the operation may or may not have committed",
        safe_action=TransportLossSafeAction.REUSE_REQUEST_IDENTITY,
        safe_action_text=(
            "reuse the same request identity and retry once; the owning store dedupes, "
            "so an already-committed effect is not applied twice"
        ),
        uncertainty_preserved=True,
    ),
    TransportLossBoundary(
        key="loss_after_returned_handle",
        title="Connection lost after a durable handle or receipt was returned",
        observed_by="client",
        classification="accepted — a durable handle proves the operation was recorded",
        safe_action=TransportLossSafeAction.QUERY_OWNER_STATUS,
        safe_action_text=(
            "query the owner status locator with the returned handle instead of "
            "re-submitting; the effect already exists"
        ),
        uncertainty_preserved=False,
    ),
    TransportLossBoundary(
        key="malformed_response",
        title="A response arrived but was malformed or unparseable",
        observed_by="client",
        classification="operation outcome unknowable — bytes arrived but cannot be trusted",
        safe_action=TransportLossSafeAction.REUSE_REQUEST_IDENTITY,
        safe_action_text=(
            "reuse the same request identity and retry once rather than assuming failure; "
            "the owning store dedupes an already-applied effect"
        ),
        uncertainty_preserved=True,
    ),
    TransportLossBoundary(
        key="server_restart_new_nonce",
        title="Server restarted (the session fingerprint returns a new nonce)",
        observed_by="client",
        classification="new process — prior in-flight acceptance is unknowable across the restart",
        safe_action=TransportLossSafeAction.QUERY_OWNER_STATUS,
        safe_action_text=(
            "query the owner status locator for the prior request identity; do not assume "
            "the pre-restart operation failed just because the nonce changed"
        ),
        uncertainty_preserved=True,
    ),
)

# Named invariant asserted PRESENT by the FR07 transport-authority validator: the
# generated client guidance must always carry the retry-once-then-record-gap rule.
RETRY_GAP_GUIDANCE = (
    "If the retry itself fails, retry once then record the gap loudly before continuing — "
    "never let a lost `trw_*` call disappear silently."
)

# Explicit denial that the server ever observes client receipt of bytes. Phrased as
# a negation so no generated text asserts server-side receipt knowledge (FR06).
_SERVER_RECEIPT_DISCLAIMER = (
    "The server never observes whether your client received the response bytes; it reports "
    "only what it committed. Transport loss is a client-side observation, so recovery is "
    "driven by request identity and the owner status locator, not by any server claim of delivery."
)


def render_transport_loss_guidance() -> str:
    """Render the client-visible transport-loss retry protocol (FR06).

    Client-agnostic guidance rendered from ``TRANSPORT_LOSS_PROTOCOL`` so every
    generated client integration snippet classifies the same four boundaries
    and states a safe, idempotent recovery for each.
    """
    lines = [
        "## MCP transport-loss retry protocol",
        "",
        _SERVER_RECEIPT_DISCLAIMER,
        "",
    ]
    for boundary in TRANSPORT_LOSS_PROTOCOL:
        uncertainty = (
            "Keep the operation outcome `uncertain` until the owner store confirms it."
            if boundary.uncertainty_preserved
            else "The operation outcome is already known; do not mark it uncertain."
        )
        lines.append(f"- **{boundary.title}** ({boundary.classification}): {boundary.safe_action_text}. {uncertainty}")
    lines.extend(["", RETRY_GAP_GUIDANCE])
    return "\n".join(lines)


def client_transport_guidance(client_id: str) -> str:
    """Return the transport-loss protocol embedded in a client's integration snippet.

    The protocol is identical for every client; this wraps it in a client-scoped
    marker so each supported profile's generated instructions include all four
    boundaries verbatim.
    """
    return f"<!-- trw:transport-loss:{client_id} -->\n{render_transport_loss_guidance()}"


# ---------------------------------------------------------------------------
# Truthful generated capability instructions (PRD-CORE-218-FR06)
#
# Client instruction projections must derive from the RESOLVED manifest/profile
# and distinguish three capability classes:
#   - available    — kernel + selected packs (usable right now)
#   - discoverable  — packs reachable via trw_skill_discovery / trw_request_tool_access
#   - gated         — operator-grant-only
#
# Two client profiles rendering the SAME resolved state must encode the same
# semantic capability truth despite format differences (NFR03). Any count drift,
# membership drift, or lifecycle drift (e.g. a retired tool still listed) between
# a projection and the resolved profile is a parity failure.
#
# The resolved profile is the input. Shard-E's FR01 surface manifest
# (``trw_mcp.server._tools.KERNEL_TOOLS`` / ``SURFACE_MANIFEST``) is the eventual
# production producer; ``resolved_profile_from_manifest_seam`` consumes it when
# present and returns ``None`` until it lands, so tests drive fixtures.
# ---------------------------------------------------------------------------


class SurfaceLifecycle(str, Enum):
    """Lifecycle of a resolved capability. Only advertisable states reach clients."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    HIDDEN = "hidden"
    RETIRED = "retired"
    REMOVED = "removed"


# States a generated projection may still advertise. Retired/removed/hidden
# capabilities must NOT appear in truthful generated instructions.
_ADVERTISABLE_LIFECYCLE: frozenset[SurfaceLifecycle] = frozenset({SurfaceLifecycle.ACTIVE, SurfaceLifecycle.DEPRECATED})


class CapabilityClass(str, Enum):
    """The three capability classes a truthful projection must distinguish."""

    AVAILABLE = "available"
    DISCOVERABLE = "discoverable"
    GATED = "gated"


class ProjectionFormat(str, Enum):
    """Format a client renders in. Format differs; semantic state must not."""

    MARKDOWN_TABLE = "markdown_table"
    BULLET_LIST = "bullet_list"


class ProjectionDriftKind(str, Enum):
    """Why a projection failed parity against the resolved profile."""

    COUNT_DRIFT = "count_drift"
    MEMBERSHIP_DRIFT = "membership_drift"
    LIFECYCLE_DRIFT = "lifecycle_drift"


@dataclass(frozen=True, slots=True)
class ResolvedCapability:
    """One tool resolved into a capability class with its lifecycle."""

    tool_id: str
    pack: str
    capability_class: CapabilityClass
    lifecycle: SurfaceLifecycle = SurfaceLifecycle.ACTIVE


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """A resolved task profile: the single source of capability truth."""

    task_type: str
    capabilities: tuple[ResolvedCapability, ...]


@dataclass(frozen=True, slots=True)
class CapabilityProjection:
    """A client-format projection of resolved capability truth.

    ``declared_counts`` and ``listed_lifecycle`` capture what a projection *says*
    so a stale, hand-edited, or drifted projection can be caught against the
    resolved profile.
    """

    client_id: str
    fmt: ProjectionFormat
    available: tuple[str, ...]
    discoverable: tuple[str, ...]
    gated: tuple[str, ...]
    declared_counts: tuple[tuple[CapabilityClass, int], ...]
    listed_lifecycle: tuple[tuple[str, SurfaceLifecycle], ...]

    def semantic_state(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """The format-independent capability truth: the three ordered class sets."""
        return (self.available, self.discoverable, self.gated)


@dataclass(frozen=True, slots=True)
class ProjectionParityFailure:
    """A typed parity failure between a projection and the resolved profile."""

    kind: ProjectionDriftKind
    detail: str


def _is_advertisable(cap: ResolvedCapability) -> bool:
    return SurfaceLifecycle(cap.lifecycle) in _ADVERTISABLE_LIFECYCLE


def _members(profile: ResolvedProfile, capability_class: CapabilityClass) -> tuple[str, ...]:
    return tuple(
        sorted(
            cap.tool_id
            for cap in profile.capabilities
            if cap.capability_class is capability_class and _is_advertisable(cap)
        )
    )


def render_capability_projection(
    profile: ResolvedProfile, *, client_id: str, fmt: ProjectionFormat
) -> CapabilityProjection:
    """Derive a truthful three-class projection from a resolved profile.

    Retired/removed/hidden capabilities are excluded (advertising stops).
    Rendering is client-agnostic in semantics: the same profile yields the same
    ``semantic_state`` for any ``client_id`` / ``fmt``.
    """
    available = _members(profile, CapabilityClass.AVAILABLE)
    discoverable = _members(profile, CapabilityClass.DISCOVERABLE)
    gated = _members(profile, CapabilityClass.GATED)
    listed_lifecycle = tuple(
        sorted((cap.tool_id, SurfaceLifecycle(cap.lifecycle)) for cap in profile.capabilities if _is_advertisable(cap))
    )
    return CapabilityProjection(
        client_id=client_id,
        fmt=fmt,
        available=available,
        discoverable=discoverable,
        gated=gated,
        declared_counts=(
            (CapabilityClass.AVAILABLE, len(available)),
            (CapabilityClass.DISCOVERABLE, len(discoverable)),
            (CapabilityClass.GATED, len(gated)),
        ),
        listed_lifecycle=listed_lifecycle,
    )


def check_projection_parity(
    profile: ResolvedProfile, projection: CapabilityProjection
) -> tuple[ProjectionParityFailure, ...]:
    """Return typed parity failures between a projection and the resolved profile.

    Fails on (a) a declared count that disagrees with the resolved truth, (b) a
    class membership that disagrees, and (c) any listed tool whose resolved
    lifecycle is non-advertisable (a retired tool still listed).
    """
    truth = render_capability_projection(profile, client_id=projection.client_id, fmt=projection.fmt)
    truth_counts = dict(truth.declared_counts)
    failures: list[ProjectionParityFailure] = []

    for capability_class, declared in projection.declared_counts:
        actual = truth_counts.get(capability_class, 0)
        if declared != actual:
            failures.append(
                ProjectionParityFailure(
                    ProjectionDriftKind.COUNT_DRIFT,
                    f"{capability_class.value} count declared {declared} != resolved {actual}",
                )
            )

    for capability_class in CapabilityClass:
        declared_members = set(getattr(projection, capability_class.value))
        actual_members = set(getattr(truth, capability_class.value))
        if declared_members != actual_members:
            failures.append(
                ProjectionParityFailure(
                    ProjectionDriftKind.MEMBERSHIP_DRIFT,
                    f"{capability_class.value} membership {sorted(declared_members)} != {sorted(actual_members)}",
                )
            )

    resolved_lifecycle = {cap.tool_id: SurfaceLifecycle(cap.lifecycle) for cap in profile.capabilities}
    for tool_id in (*projection.available, *projection.discoverable, *projection.gated):
        lifecycle = resolved_lifecycle.get(tool_id)
        if lifecycle is not None and lifecycle not in _ADVERTISABLE_LIFECYCLE:
            failures.append(
                ProjectionParityFailure(
                    ProjectionDriftKind.LIFECYCLE_DRIFT,
                    f"{tool_id} is {lifecycle.value} but still listed in the projection",
                )
            )

    return tuple(failures)


def render_client_capability_instructions(profile: ResolvedProfile, *, client_id: str) -> str:
    """Render human-readable client instructions distinguishing the three classes."""
    proj = render_capability_projection(profile, client_id=client_id, fmt=ProjectionFormat.BULLET_LIST)
    labels = (
        (proj.available, "Available now (kernel + selected packs)"),
        (proj.discoverable, "Discoverable via trw_skill_discovery / trw_request_tool_access"),
        (proj.gated, "Operator-grant only"),
    )
    lines = [f"<!-- trw:capabilities:{client_id} -->", f"## Resolved capabilities ({profile.task_type})", ""]
    lines += [f"- **{label}** ({len(members)}): {', '.join(members) or '(none)'}" for members, label in labels]
    return "\n".join(lines)


def resolved_profile_from_manifest_seam(task_type: str) -> ResolvedProfile | None:
    """Build a ``ResolvedProfile`` from shard-E's FR01 surface manifest, if present.

    Returns ``None`` when ``trw_mcp.server._tools`` does not yet export the
    ``KERNEL_TOOLS`` / ``SURFACE_MANIFEST`` names (FR01 not landed) or when their
    shape is not the expected iterable of resolved entries. This is the
    consumption seam FR06 hands to FR01 — no manifest data is fabricated here.
    """
    try:
        from trw_mcp.server import _tools as _server_tools

        kernel_tools = _server_tools.KERNEL_TOOLS
        surface_manifest = _server_tools.SURFACE_MANIFEST
    except (ImportError, AttributeError):
        return None
    try:
        capabilities = tuple(
            ResolvedCapability(
                str(entry["tool_id"]),
                "kernel" if str(entry["tool_id"]) in kernel_tools else str(entry.get("pack", "")),
                CapabilityClass(str(entry["capability_class"])),
                SurfaceLifecycle(str(entry.get("lifecycle", "active"))),
            )
            for entry in surface_manifest
        )
    except (TypeError, KeyError, ValueError):
        return None
    return ResolvedProfile(task_type=task_type, capabilities=capabilities)
