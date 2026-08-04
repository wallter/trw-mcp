"""Every client's evidence marker is a path TRW itself writes. Pinned, not fixed.

``_file_evidenced_clients`` exists to find "clients with project-scoped evidence
TRW did not fabricate" — its own docstring. Measured against the client-profile
catalog, **7 of its 8 markers are paths TRW scaffolds for that same client**, and
``_trw_scaffolds_marker`` rejects only 2 of them.

Why only 2. The predicate rejects a marker overlapping a framework-CORE surface
(that catches ``.claude``) or a surface TRW installs for a DIFFERENT client (that
catches ``.cursor``, because cursor-cli writes underneath it). It has no arm for
the case that actually matters: a marker TRW writes for the client it is evidence
*for*. So ``.github/agents`` evidences copilot while TRW writes ``.github/agents``
for copilot, ``.codex`` evidences codex while TRW writes ``.codex/agents``, and so
on.

**This is latent, not live, and the distinction is the point.** Reaching it needs
TRW to write client X's marker in a project where X is not recorded. That path was
closed when ``_run_post_update_phases`` started deriving its write targets from
``recorded or ide_targets`` rather than from detection. Reproduced at HEAD:
``init-project --ide codex`` then three bare ``update-project`` runs leaves the
record ``['codex']`` and never creates ``.cursor/cli.json``.

**Why this is a test and not a fix.** Applying the predicate's own stated rule
honestly would reject every marker in the table, because every one of them is
TRW-written — which would make ``_file_evidenced_clients`` permanently empty and
turn a live mechanism into dead code. That is a design decision (retire the
mechanism, or find each client a marker TRW genuinely never writes), not a
tail-end edit. Pinning the measurement here means the next person inherits the
contradiction with numbers instead of rediscovering it.

Found by an independent adversarial review (a different vendor's model) of the
commit that claimed this exclusion was "computed" and total. It is computed. It is
not total.
"""

from __future__ import annotations


def _self_scaffolded(client: str, marker: str) -> bool:
    """True when TRW writes *marker*, or something under it, for *client* itself."""
    from trw_mcp.client_profiles.catalog import client_scaffold_relpaths

    own = set(client_scaffold_relpaths(client))
    prefix = marker.rstrip("/") + "/"
    return any(path == marker or path.startswith(prefix) for path in own)


def test_the_scaffold_lookup_sees_a_known_path() -> None:
    """Non-vacuity control. A lookup that returned nothing would make this file green and empty."""
    assert _self_scaffolded("copilot", ".github/agents"), "the scaffold lookup no longer resolves"
    assert not _self_scaffolded("claude-code", ".github/agents"), "the lookup is not client-scoped"


def test_the_predicate_still_rejects_the_two_it_was_built_for() -> None:
    """Regression guard for the fix that IS in place — core and cross-client overlap."""
    from trw_mcp.bootstrap._template_claude_md import _trw_scaffolds_marker

    assert _trw_scaffolds_marker("claude-code", ".claude"), "core-surface rejection regressed"
    assert _trw_scaffolds_marker("cursor-ide", ".cursor"), "cross-client rejection regressed"


def test_the_self_scaffolded_gap_is_recorded_rather_than_believed_closed() -> None:
    """The measurement, pinned so it cannot drift silently in either direction.

    If someone adds the missing arm to ``_trw_scaffolds_marker``, this test fails
    and they must come here and say what happened to the mechanism — which is the
    conversation the change requires. If someone adds a NEW client whose marker is
    also self-scaffolded, the count moves and this fails too.
    """
    from trw_mcp.bootstrap._template_claude_md import _CLIENT_EVIDENCE_MARKERS, _trw_scaffolds_marker

    accepted_but_self_scaffolded = sorted(
        f"{client}:{marker}"
        for client, markers in _CLIENT_EVIDENCE_MARKERS.items()
        for marker in markers
        if _self_scaffolded(client, marker) and not _trw_scaffolds_marker(client, marker)
    )

    assert accepted_but_self_scaffolded == [
        "antigravity-cli:ANTIGRAVITY.md",
        "codex:.codex",
        "copilot:.github/agents",
        "cursor-cli:.cursor/cli.json",
        "opencode:.opencode",
        "opencode:opencode.json",
    ], (
        "the set of markers TRW writes for the client they evidence has changed. That set is "
        "supposed to be EMPTY; it is recorded here because emptying it would make "
        "_file_evidenced_clients permanently empty, which is a design decision and not a patch. "
        f"Now: {accepted_but_self_scaffolded}"
    )
