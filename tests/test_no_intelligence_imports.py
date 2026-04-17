"""Import guard regression test — backend-only intelligence surfaces.

PRD-INFRA-054 restores the thin public client boundary: local business-policy
intelligence must not ship in trw-mcp. The backend-only surfaces include
``state.bandit_policy``, ``state.meta_synthesis``, and ``scoring.attribution``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Root of the trw_mcp source tree
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trw_mcp"

_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "from trw_mcp.state.bandit_policy",
    "import trw_mcp.state.bandit_policy",
    "from trw_mcp.scoring.attribution",
    "import trw_mcp.scoring.attribution",
    "from trw_mcp.state.meta_synthesis",
    "import trw_mcp.state.meta_synthesis",
)

_VIOLATION_MSG = (
    "Intelligence import found in {filepath}:{lineno}: {line}\n"
    "Heavy intelligence surfaces (bandit_policy, meta_synthesis, attribution) "
    "are backend-only."
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
        violations = [v for v in _scan_source_files() if "scoring.attribution" in v]
        assert violations == [], "\n".join(violations)

    def test_no_bandit_policy_imports(self) -> None:
        """No source file imports from state.bandit_policy (backend-only)."""
        violations = [v for v in _scan_source_files() if "state.bandit_policy" in v]
        assert violations == [], "\n".join(violations)

    def test_no_meta_synthesis_imports(self) -> None:
        """No source file imports from state.meta_synthesis (backend-only)."""
        violations = [v for v in _scan_source_files() if "meta_synthesis" in v]
        assert violations == [], "\n".join(violations)

    def test_no_intelligence_files_in_source_tree(self) -> None:
        """Backend-only intelligence module files must not exist in the source tree."""
        forbidden_files = [
            _SRC_ROOT / "state" / "bandit_policy.py",
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

    def test_no_meta_tune_intelligence_imports(self) -> None:
        """The public package no longer ships a local meta_tune implementation."""
        meta_tune_path = _SRC_ROOT / "tools" / "meta_tune.py"
        assert not meta_tune_path.exists(), f"Backend-only intelligence file still exists: {meta_tune_path}"

    def test_import_trw_mcp_succeeds(self) -> None:
        """import trw_mcp works after intelligence code removal."""
        import trw_mcp  # noqa: F401

    def test_deleted_modules_raise_import_error(self) -> None:
        """Importing deleted backend-only intelligence modules raises ImportError."""
        with pytest.raises(ImportError):
            from trw_mcp.state.bandit_policy import WithholdingPolicy  # type: ignore[import-not-found]  # noqa: F401

        with pytest.raises(ImportError):
            from trw_mcp.state.meta_synthesis import ALLOWED_KNOBS  # type: ignore[import-not-found]  # noqa: F401
