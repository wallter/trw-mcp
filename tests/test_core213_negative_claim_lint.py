"""PRD-CORE-213 FR06-FR07 — negative-claim rule in agents + advisory lint.

FR06: the four bundled auditor/reviewer/researcher agent definitions carry the
mandatory negative-existence-claim evidence rule.
FR07: ``scripts/check-negative-claims.py`` flags unsupported negative claims in
scoped audit docs (report-only default; ``--strict`` exit 1).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "check-negative-claims.py"
_AGENTS_DIR = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"
_AGENT_FILES = ("trw-auditor.md", "trw-adversarial-auditor.md", "trw-researcher.md", "trw-reviewer.md")


def _load_lint():  # type: ignore[no-untyped-def]
    import sys

    spec = importlib.util.spec_from_file_location("check_negative_claims", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass Finding can resolve annotations
    # (dataclasses looks the class module up in sys.modules).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# FR06 — agent rule sentinel
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("agent_file", _AGENT_FILES)
def test_agent_files_carry_rule(agent_file: str) -> None:
    text = (_AGENTS_DIR / agent_file).read_text(encoding="utf-8")
    assert "negative existence claim" in text
    assert "trw_code_search" in text
    assert "grep" in text
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    granted_tools = frontmatter.get("tools", frontmatter.get("allowedTools", []))
    assert "mcp__trw__trw_code_search" in granted_tools


# --------------------------------------------------------------------------- #
# FR07 — lint behavior
# --------------------------------------------------------------------------- #


def _write_scope(tmp_path: Path) -> Path:
    scope = tmp_path / "scope.yaml"
    scope.write_text("globs:\n  - docs/research/**/audit*.md\n", encoding="utf-8")
    return scope


def _write_audit(tmp_path: Path, name: str, body: str) -> None:
    audit = tmp_path / "docs" / "research" / "x"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / name).write_text(body, encoding="utf-8")


def test_flags_unsupported_claim(tmp_path: Path) -> None:
    lint = _load_lint()
    _write_audit(tmp_path, "audit-1.md", "The function foo has no callers exist in the tree.\n")
    scope = _write_scope(tmp_path)
    rc = lint.main(["--scope", str(scope), "--root", str(tmp_path), "--strict"])
    assert rc == 1


def test_supported_claim_with_command_and_root_proof_not_flagged(tmp_path: Path) -> None:
    lint = _load_lint()
    body = (
        "no callers found (`trw_code_search(pattern='foo', root='trw-mcp/src')`, root confirmed via `ls trw-mcp/src`)\n"
    )
    _write_audit(tmp_path, "audit-2.md", body)
    scope = _write_scope(tmp_path)
    rc = lint.main(["--scope", str(scope), "--root", str(tmp_path), "--strict"])
    assert rc == 0


def test_adjacent_line_evidence_suppresses(tmp_path: Path) -> None:
    lint = _load_lint()
    body = "no callers found for symbol bar.\n`trw_code_search(pattern='bar', root='trw-mcp/src')`\n"
    _write_audit(tmp_path, "audit-3.md", body)
    scope = _write_scope(tmp_path)
    assert lint.main(["--scope", str(scope), "--root", str(tmp_path), "--strict"]) == 0


def test_default_mode_exit_zero_regardless(tmp_path: Path) -> None:
    lint = _load_lint()
    _write_audit(tmp_path, "audit-4.md", "no callers exist and nothing references it.\n")
    scope = _write_scope(tmp_path)
    # no --strict -> report-only, exit 0 even with findings.
    assert lint.main(["--scope", str(scope), "--root", str(tmp_path)]) == 0


def test_ratchet_exit_one_above_baseline(tmp_path: Path) -> None:
    lint = _load_lint()
    _write_audit(tmp_path, "audit-5.md", "no callers exist.\ndoes not exist anywhere.\n")
    scope = _write_scope(tmp_path)
    # 2 findings, baseline 1 -> fail.
    assert lint.main(["--scope", str(scope), "--root", str(tmp_path), "--ratchet", "1"]) == 1
    # baseline 5 -> pass.
    assert lint.main(["--scope", str(scope), "--root", str(tmp_path), "--ratchet", "5"]) == 0


def test_missing_scope_config_exit_two(tmp_path: Path) -> None:
    lint = _load_lint()
    assert lint.main(["--scope", str(tmp_path / "nope.yaml"), "--root", str(tmp_path), "--strict"]) == 2


def test_negative_claim_outside_scope_not_scanned(tmp_path: Path) -> None:
    lint = _load_lint()
    # A README (not an audit doc) with a negative claim is out of scope.
    (tmp_path / "README.md").write_text("no callers exist here.\n", encoding="utf-8")
    scope = _write_scope(tmp_path)
    assert lint.main(["--scope", str(scope), "--root", str(tmp_path), "--strict"]) == 0
