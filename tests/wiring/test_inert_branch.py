"""FR05 — inert branch: reachable, registered, structurally incapable of a result."""

from __future__ import annotations

import ast
from pathlib import Path

from trw_mcp.wiring.checks.inert import check_inert, is_constant_empty
from trw_mcp.wiring.config import DEFAULT_CONFIG
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import ArtifactContract, build_registry

FIXTURE_KEY = (
    "INERT_BRANCH::callsite:inert-required-inputs:trw-mcp/src/trw_mcp/tools/code_search.py:rank_semantic_chunks"
)


def test_semantic_search_branch_flagged_inert(live_result: DetectorResult) -> None:
    """``rank_semantic_chunks(query=query, chunks=(), embedder=None)``."""
    finding = next(f for f in live_result.findings if f.key == FIXTURE_KEY)
    assert finding.edge_class is EdgeClass.INERT_BRANCH
    assert "chunks=()" in finding.evidence and "embedder=None" in finding.evidence
    assert "rank_semantic_chunks" in finding.producer_side


def test_exactly_one_inert_finding_across_trw_mcp(live_result: DetectorResult) -> None:
    """Zero over-fire across the whole package.

    ~1,000 modules and one finding. That number is the check's whole value
    proposition: the naive version of the adjacent registrar check measured a
    47% false-positive rate on this same codebase.
    """
    inert = [f for f in live_result.findings if f.edge_class is EdgeClass.INERT_BRANCH]
    assert len(inert) == 1, f"expected exactly one inert branch, got: {[f.key for f in inert]}"


def _callsite_contract(repo_root: Path, scan_root: str, package: str) -> ArtifactContract:
    template = next(
        c for c in build_registry(repo_root) if c.kind is ContractKind.CALL_SITE and c.detail_value("scan_root")
    )
    return ArtifactContract(
        contract_id=template.contract_id,
        kind=template.kind,
        producer=template.producer,
        consumer=template.consumer,
        artifact=template.artifact,
        detail=(("scan_root", scan_root), ("package", package)),
    )


def _synthetic(tmp_path: Path, caller_body: str, callee_body: str) -> Path:
    package = tmp_path / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "callee.py").write_text(callee_body, encoding="utf-8")
    (package / "caller.py").write_text(caller_body, encoding="utf-8")
    return tmp_path


def test_optional_parameters_are_not_inert(repo_root: Path, tmp_path: Path) -> None:
    """Passing ``None`` to a parameter that already defaults to ``None`` is a no-op, not a defect."""
    root = _synthetic(
        tmp_path,
        "from pkg.callee import work\n\ndef run():\n    return work(items=(), flag=None)\n",
        "def work(*, items=(), flag=None):\n    return items\n",
    )
    findings = check_inert(
        root,
        _callsite_contract(repo_root, "pkg", "pkg"),
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
        min_empty_kwargs=DEFAULT_CONFIG.inert_min_empty_kwargs,
    )
    assert not findings, [f.render() for f in findings]


def test_single_empty_argument_is_not_enough(repo_root: Path, tmp_path: Path) -> None:
    """One ``x=None`` is an ordinary call. The floor is deliberate under-claiming."""
    root = _synthetic(
        tmp_path,
        "from pkg.callee import work\n\ndef run():\n    return work(items=(), name='x')\n",
        "def work(*, items, name):\n    return items\n",
    )
    findings = check_inert(
        root,
        _callsite_contract(repo_root, "pkg", "pkg"),
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
        min_empty_kwargs=DEFAULT_CONFIG.inert_min_empty_kwargs,
    )
    assert not findings


def test_dynamic_dispatch_prefers_under_claiming(repo_root: Path, tmp_path: Path) -> None:
    """An unresolvable callee yields no finding, rather than a guessed one.

    Bare-symbol matching is banned: three unrelated features in this repository
    are named ``meta_tune``, so a name match is not evidence of identity.
    """
    root = _synthetic(
        tmp_path,
        "def run(dispatch):\n    handler = dispatch['work']\n    return handler(items=(), flag=None)\n",
        "def work(*, items, flag):\n    return items\n",
    )
    findings = check_inert(
        root,
        _callsite_contract(repo_root, "pkg", "pkg"),
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
        min_empty_kwargs=DEFAULT_CONFIG.inert_min_empty_kwargs,
    )
    assert not findings


def test_synthetic_inert_call_is_detected(repo_root: Path, tmp_path: Path) -> None:
    """The positive control for the three negatives above."""
    root = _synthetic(
        tmp_path,
        "from pkg.callee import work\n\ndef run():\n    return work(items=(), flag=None)\n",
        "def work(*, items, flag):\n    return items\n",
    )
    findings = check_inert(
        root,
        _callsite_contract(repo_root, "pkg", "pkg"),
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
        min_empty_kwargs=DEFAULT_CONFIG.inert_min_empty_kwargs,
    )
    assert [f.edge_class for f in findings] == [EdgeClass.INERT_BRANCH]
    assert findings[0].contract_id.endswith(":work")


def test_constant_empty_classification() -> None:
    for source, expected in (
        ("()", True),
        ("[]", True),
        ("{}", True),
        ("None", True),
        ("''", True),
        ("tuple()", True),
        ("(1,)", False),
        ("'x'", False),
        ("compute()", False),
        ("0", False),
    ):
        node = ast.parse(source, mode="eval").body
        assert is_constant_empty(node) is expected, source
