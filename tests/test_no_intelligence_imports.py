"""Import guard regression test — backend-only intelligence surfaces.

PRD-CORE-105 remediation: ``bandit_policy`` is restored as a LOCAL-FIRST
module in trw-mcp (Vision Principle 6). The heavy intelligence surfaces that
remain backend-only are ``scoring.attribution`` and ``state.meta_synthesis``.

Guards that previously blocked ``bandit_policy`` are updated to reflect the
restored local-first bandit path. The ``meta_synthesis`` and ``attribution``
guards remain in place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Root of the trw_mcp source tree
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"

# Forbidden import patterns — backend-only intelligence surfaces
# Note: bandit_policy is LOCAL-FIRST and is intentionally present (PRD-CORE-105).
_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "from trw_mcp.scoring.attribution",
    "import trw_mcp.scoring.attribution",
    "from trw_mcp.state.meta_synthesis",
    "import trw_mcp.state.meta_synthesis",
)

_VIOLATION_MSG = (
    "Intelligence import found in {filepath}:{lineno}: {line}\n"
    "Heavy intelligence surfaces (meta_synthesis, attribution) are backend-only. "
    "bandit_policy is local-first and is permitted (PRD-CORE-105)."
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
    """Guard against backend-only intelligence code reintroduction in trw-mcp source."""

    def test_no_attribution_imports(self) -> None:
        """No source file imports from scoring.attribution (backend-only)."""
        violations = [
            v for v in _scan_source_files() if "scoring.attribution" in v
        ]
        assert violations == [], "\n".join(violations)

    def test_no_meta_synthesis_imports(self) -> None:
        """No source file imports from state.meta_synthesis (backend-only)."""
        violations = [
            v for v in _scan_source_files() if "meta_synthesis" in v
        ]
        assert violations == [], "\n".join(violations)

    def test_no_intelligence_files_in_source_tree(self) -> None:
        """Backend-only intelligence module files must not exist in the source tree."""
        forbidden_files = [
            _SRC_ROOT / "state" / "meta_synthesis.py",
            _SRC_ROOT / "tools" / "meta_tune.py",
        ]
        forbidden_dirs = [
            _SRC_ROOT / "scoring" / "attribution",
        ]
        for f in forbidden_files:
            assert not f.exists(), f"Backend-only intelligence file still exists: {f}"
        for d in forbidden_dirs:
            assert not d.exists(), f"Backend-only intelligence directory still exists: {d}"

    def test_bandit_policy_is_local_first(self) -> None:
        """PRD-CORE-105: bandit_policy is a local-first module and MUST exist."""
        bandit_policy_path = _SRC_ROOT / "state" / "bandit_policy.py"
        assert bandit_policy_path.exists(), (
            "bandit_policy.py must exist for local-first bandit behavior (PRD-CORE-105)"
        )

    def test_bandit_policy_imports_successfully(self) -> None:
        """PRD-CORE-105: WithholdingPolicy can be imported from local bandit_policy."""
        from trw_mcp.state.bandit_policy import WithholdingPolicy  # noqa: F401
        assert WithholdingPolicy is not None

    def test_import_trw_mcp_succeeds(self) -> None:
        """import trw_mcp works after intelligence code restoration."""
        import trw_mcp  # noqa: F401

    def test_deleted_modules_raise_import_error(self) -> None:
        """Importing deleted backend-only intelligence modules raises ImportError."""
        with pytest.raises(ImportError):
            from trw_mcp.state.meta_synthesis import ALLOWED_KNOBS  # type: ignore[import-not-found]  # noqa: F401

