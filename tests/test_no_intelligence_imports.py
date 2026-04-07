"""Import guard regression test — prevents intelligence code reintroduction.

PRD-INFRA-054 FR10: Scans all .py files under trw-mcp/src/trw_mcp/ and
fails if any forbidden intelligence import pattern is found.

Intelligence code (bandit policy, causal attribution, meta-tune synthesis)
was extracted to the backend in PRD-INFRA-052 and removed from trw-mcp in
PRD-INFRA-054.  This test ensures it is never reintroduced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Root of the trw_mcp source tree
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"

# Forbidden import patterns — any occurrence in source code is a violation
_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "from trw_mcp.scoring.attribution",
    "import trw_mcp.scoring.attribution",
    "from trw_mcp.state.bandit_policy",
    "import trw_mcp.state.bandit_policy",
    "from trw_mcp.state.meta_synthesis",
    "import trw_mcp.state.meta_synthesis",
)

_VIOLATION_MSG = (
    "Intelligence import found in {filepath}:{lineno}: {line}\n"
    "Intelligence code was extracted to the backend in PRD-INFRA-052. "
    "See PRD-INFRA-054."
)


def _scan_source_files() -> list[str]:
    """Scan all .py source files for forbidden intelligence imports.

    Returns a list of violation messages (empty if clean).
    """
    violations: list[str] = []
    for py_file in sorted(_SRC_ROOT.rglob("*.py")):
        # Skip __pycache__ directories
        if "__pycache__" in str(py_file):
            continue
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith("#"):
                continue
            for pattern in _FORBIDDEN_PATTERNS:
                if pattern in stripped:
                    violations.append(
                        _VIOLATION_MSG.format(
                            filepath=py_file.relative_to(_SRC_ROOT.parent.parent),
                            lineno=lineno,
                            line=stripped,
                        )
                    )
    return violations


class TestNoIntelligenceImports:
    """Guard against intelligence code reintroduction in trw-mcp source."""

    def test_no_attribution_imports(self) -> None:
        """No source file imports from scoring.attribution."""
        violations = [
            v for v in _scan_source_files() if "scoring.attribution" in v
        ]
        assert violations == [], "\n".join(violations)

    def test_no_bandit_policy_imports(self) -> None:
        """No source file imports from state.bandit_policy."""
        violations = [
            v for v in _scan_source_files() if "bandit_policy" in v
        ]
        assert violations == [], "\n".join(violations)

    def test_no_meta_synthesis_imports(self) -> None:
        """No source file imports from state.meta_synthesis."""
        violations = [
            v for v in _scan_source_files() if "meta_synthesis" in v
        ]
        assert violations == [], "\n".join(violations)

    def test_no_intelligence_files_in_source_tree(self) -> None:
        """FR12: No intelligence module files exist in the source tree."""
        forbidden_files = [
            _SRC_ROOT / "state" / "bandit_policy.py",
            _SRC_ROOT / "state" / "meta_synthesis.py",
            _SRC_ROOT / "tools" / "meta_tune.py",
        ]
        forbidden_dirs = [
            _SRC_ROOT / "scoring" / "attribution",
        ]
        for f in forbidden_files:
            assert not f.exists(), f"Intelligence file still exists: {f}"
        for d in forbidden_dirs:
            assert not d.exists(), f"Intelligence directory still exists: {d}"

    def test_import_trw_mcp_succeeds(self) -> None:
        """FR07/FR12: import trw_mcp works after intelligence code removal."""
        import trw_mcp  # noqa: F401

    def test_deleted_modules_raise_import_error(self) -> None:
        """Importing deleted intelligence modules raises ImportError."""
        with pytest.raises(ImportError):
            from trw_mcp.state.bandit_policy import WithholdingPolicy  # type: ignore[import-not-found]  # noqa: F401

        with pytest.raises(ImportError):
            from trw_mcp.state.meta_synthesis import ALLOWED_KNOBS  # type: ignore[import-not-found]  # noqa: F401
