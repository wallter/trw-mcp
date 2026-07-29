"""FR02 — artifact existence. The only signature that catches ``channels``."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.wiring.checks.existence import check_existence
from trw_mcp.wiring.detector import run_detector
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import build_registry


def test_never_fired_is_detected_from_a_synthetic_manifest(tmp_path: Path) -> None:
    """The detector still catches a never-rendered channel, on demand.

    This asserted that `channel:cc-02-claude-md-distill-segment` and
    `channel:codex-agents-md-hotspots` appear in the LIVE result. PRD-CORE-239
    FR01 removed both, and with them the detector's only NEVER_FIRED specimens —
    so fixing the defect deleted the test for the detector that found it.

    A detector test must not depend on a live defect remaining unfixed. This
    builds a manifest declaring a marker-replace channel whose marker never
    landed in its target file, which is the NEVER_FIRED contract itself, and
    requires the detector to say so. It keeps working no matter what the
    repository's real channel set becomes.
    """
    manifest = tmp_path / ".trw" / "channels" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: synthetic-never-fired
    client: claude-code
    surface: instruction_file_segment
    telemetry_tag: synthetic-never-fired
    file: CLAUDE.md
    tier_default: T2
    status: active
    write_strategy: MARKER_REPLACE
    markers:
      start: "<!-- synthetic:start -->"
      end: "<!-- synthetic:end -->"
""",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").write_text("# no marker here\n", encoding="utf-8")

    result = run_detector(tmp_path)

    never_fired = {f.contract_id for f in result.findings if f.edge_class is EdgeClass.NEVER_FIRED}
    assert "channel:synthetic-never-fired" in never_fired, (
        f"the detector no longer recognises a declared-but-unrendered channel; found {sorted(never_fired)}"
    )

    finding = next(f for f in result.findings if f.contract_id == "channel:synthetic-never-fired")
    # NFR04: a reader must be able to check the claim without re-deriving it.
    assert "CLAUDE.md" in finding.evidence
    assert "synthetic:start" in finding.evidence


def test_consumer_orphan_names_the_absent_producer(tmp_path: Path) -> None:
    """A consumer whose sidecar nothing can emit is flagged, and the evidence says so.

    This ran against the LIVE result and keyed on `sidecar:entity-risk-map`,
    which was the detector's only CONSUMER_ORPHAN specimen. Removing
    `trw_entity_risk_map` (UF-011, decided 2026-07-29) would have deleted the
    test along with the defect — exactly how FR01 blinded the NEVER_FIRED check,
    where a gate kept passing because its subject set had emptied.

    So it is synthetic now: a contract naming a producer token that appears in
    no source, checked on demand. The class stays provably detectable no matter
    what the repository's real contracts become.
    """
    from trw_mcp.wiring.registry import ArtifactContract

    contract = ArtifactContract(
        contract_id="sidecar:synthetic-orphan",
        kind=ContractKind.SIDECAR,
        producer="a producer that does not exist",
        consumer="trw-mcp/src/trw_mcp/tools/synthetic_consumer.py",
        artifact=".trw/distill/map-cache/synthetic-orphan-*.json",
        detail=(
            ("producer_token", "zzz-no-such-producer-token-zzz"),
            ("search_root", "trw-mcp/src"),
        ),
    )

    findings = check_existence(tmp_path, (contract,))

    finding = next(f for f in findings if f.contract_id == "sidecar:synthetic-orphan")
    assert finding.edge_class is EdgeClass.CONSUMER_ORPHAN
    assert "synthetic-orphan-*.json" in finding.evidence
    assert "nothing in this repository can emit" in finding.evidence


def test_rendered_channel_produces_no_finding(tmp_path: Path) -> None:
    """A channel whose marker IS present is not flagged — both branches, synthetically.

    This used to select a marker-bearing channel from the LIVE registry. After
    FR01 no surviving channel declares a marker, so the selection returned None
    and the test `pytest.skip`ped on every run — while still containing two
    assertions naming channels FR01 deleted, one of which (`in flagged`) would
    have FAILED had it ever executed. A skip hid a broken test whose stated
    purpose was proving the check is not vacuous.

    Building both channels here means the positive and negative branch are
    asserted against the same run, and neither depends on what the repository's
    real channel set happens to be.
    """
    manifest = tmp_path / ".trw" / "channels" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: synthetic-rendered
    client: claude-code
    surface: instruction_file_segment
    telemetry_tag: synthetic-rendered
    file: CLAUDE.md
    tier_default: T2
    status: active
    write_strategy: MARKER_REPLACE
    markers:
      start: "<!-- rendered:start -->"
      end: "<!-- rendered:end -->"
  - id: synthetic-unrendered
    client: claude-code
    surface: instruction_file_segment
    telemetry_tag: synthetic-unrendered
    file: CLAUDE.md
    tier_default: T2
    status: active
    write_strategy: MARKER_REPLACE
    markers:
      start: "<!-- unrendered:start -->"
      end: "<!-- unrendered:end -->"
""",
        encoding="utf-8",
    )
    # Only the first channel's marker actually lands in the target file.
    (tmp_path / "CLAUDE.md").write_text(
        "# stub\n<!-- rendered:start -->\nreal content\n<!-- rendered:end -->\n",
        encoding="utf-8",
    )

    contracts = [c for c in build_registry(tmp_path) if c.kind is ContractKind.CHANNEL_RENDER]
    findings = check_existence(tmp_path, tuple(contracts))
    flagged = {f.contract_id for f in findings if f.edge_class is EdgeClass.NEVER_FIRED}

    assert "channel:synthetic-rendered" not in flagged, "a channel whose marker IS present must not be flagged"
    assert "channel:synthetic-unrendered" in flagged, (
        "the check is per-contract, not all-or-nothing — a sibling whose marker "
        "was never written must still fire, or the negative case above proves nothing"
    )
