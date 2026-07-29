"""FR08 — the detector asserts its own invocation.

The precedent this guards against is exact: ``dead-code-audit.json``, generated
2026-03-29 with 22 findings including 3 P0s, indexed as a spec artifact, never
run again, and 117 days later at least seven findings still open. Discipline did
not keep that alive and will not keep this alive either. A failing assertion
will.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import build_registry
from trw_mcp.wiring.selfcheck import ADVISORY_FLAG, CLI_MODULE, check_self_invocation

SELF_CONTRACT_ID = "self:wiring-detector-invocation"


def _self_contract(repo_root: Path) -> object:
    return next(c for c in build_registry(repo_root) if c.kind is ContractKind.DETECTOR_SELF)


def _stub_root(tmp_path: Path, makefile_text: str) -> Path:
    (tmp_path / "Makefile").write_text(makefile_text, encoding="utf-8")
    return tmp_path


WIRED_MAKEFILE = f"check: lint wiring-gate test\n\nwiring-gate:\n\t@python -m {CLI_MODULE} --repo-root .\n"


def test_detector_asserts_own_invocation(live_result: DetectorResult, repo_root: Path) -> None:
    """On the real repository, right now, the detector IS wired into ``make check``."""
    assert not [f for f in live_result.findings if f.contract_id == SELF_CONTRACT_ID], (
        "the wiring detector is not enforced by make check"
    )
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    assert "wiring-gate" in makefile and CLI_MODULE in makefile


def test_self_check_fails_when_the_makefile_line_is_removed(repo_root: Path, tmp_path: Path) -> None:
    """Deleting the invocation is the rollback path — and it fails loudly."""
    root = _stub_root(tmp_path, WIRED_MAKEFILE.replace("check: lint wiring-gate test", "check: lint test"))
    findings = check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]
    assert [f.edge_class for f in findings] == [EdgeClass.NEVER_FIRED]
    assert "not a prerequisite of the 'check' target" in findings[0].evidence


def test_self_check_fails_when_the_target_runs_something_else(repo_root: Path, tmp_path: Path) -> None:
    """A target that exists but does not run the detector is the same defect in a nicer costume."""
    root = _stub_root(tmp_path, "check: wiring-gate\n\nwiring-gate:\n\t@echo skipping\n")
    findings = check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]
    assert [f.edge_class for f in findings] == [EdgeClass.NEVER_FIRED]
    assert "does not invoke" in findings[0].evidence


def test_self_check_rejects_advisory_in_ci(repo_root: Path, tmp_path: Path) -> None:
    """Putting ``--advisory`` in the Makefile silently converts the gate back into a report."""
    root = _stub_root(tmp_path, WIRED_MAKEFILE.replace("--repo-root .", f"{ADVISORY_FLAG} --repo-root ."))
    findings = check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]
    assert [f.edge_class for f in findings] == [EdgeClass.NEVER_FIRED]
    assert ADVISORY_FLAG in findings[0].evidence


@pytest.mark.parametrize(
    "obfuscated",
    ['--advi""sory', "--advi''sory", '--"advisory"', "--advi\\sory", "--advi\\\n\tsory"],
    ids=["double-quote-split", "single-quote-split", "quoted-flag", "backslash-escape", "line-continuation"],
)
def test_self_check_rejects_shell_obfuscated_advisory(repo_root: Path, tmp_path: Path, obfuscated: str) -> None:
    """Quoting hides the flag from a substring search but not from the shell.

    ``--advi""sory`` is one keystroke, evaluates to ``--advisory`` at execution,
    and — against the raw recipe text — leaves the literal flag absent. That is
    the naive-substring-matching defect this repo keeps rediscovering
    (``.claude/rules/trw-mcp-python.md``, Marker / Sentinel Matching), applied to
    the one check keeping this gate enforcing.
    """
    root = _stub_root(tmp_path, WIRED_MAKEFILE.replace("--repo-root .", f"{obfuscated} --repo-root ."))
    # Non-vacuity: the literal flag really is absent from the Makefile text.
    assert ADVISORY_FLAG not in (root / "Makefile").read_text(encoding="utf-8")

    findings = check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]
    assert [f.edge_class for f in findings] == [EdgeClass.NEVER_FIRED]
    assert ADVISORY_FLAG in findings[0].evidence


def test_self_check_still_accepts_an_unobfuscated_wired_recipe(repo_root: Path, tmp_path: Path) -> None:
    """Normalization must not manufacture a finding on a quoted-but-clean recipe."""
    root = _stub_root(tmp_path, WIRED_MAKEFILE.replace("--repo-root .", '--repo-root "$(CURDIR)"'))
    assert not check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]


def test_self_check_passes_on_a_correctly_wired_makefile(repo_root: Path, tmp_path: Path) -> None:
    root = _stub_root(tmp_path, WIRED_MAKEFILE)
    assert not check_self_invocation(root, _self_contract(repo_root))  # type: ignore[arg-type]
