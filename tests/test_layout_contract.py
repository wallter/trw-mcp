"""Package subjects remain testable without silently skipping broken canons."""

import ast
import runpy
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT


@pytest.mark.parametrize("name", ["arbitrary-checkout", "trw-mcp/trw-mcp"])
def test_standalone_package_has_no_monorepo(tmp_path: Path, name: str) -> None:
    package = tmp_path / name
    state = _load_layout(package)
    assert state["PACKAGE_ROOT"] == package
    assert state["MONOREPO_ROOT"] is None
    assert state["requires_monorepo"].args == (True,)


def test_independent_identity_does_not_hide_missing_checked_files(tmp_path: Path) -> None:
    (tmp_path / "release-packages.yaml").write_text("packages: {}\n")
    state = _load_layout(tmp_path / "trw-mcp")
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".claude").exists()
    assert state["MONOREPO_ROOT"] == tmp_path
    assert state["requires_monorepo"].args == (False,)


def test_legacy_identity_does_not_hide_missing_release_manifest(tmp_path: Path) -> None:
    for package in ("trw-mcp", "trw-memory"):
        directory = tmp_path / package
        directory.mkdir()
        (directory / "pyproject.toml").write_text("")
    (tmp_path / "CLAUDE.md").write_text("")
    assert _load_layout(tmp_path / "trw-mcp")["MONOREPO_ROOT"] == tmp_path


def test_installed_package_is_not_a_monorepo(tmp_path: Path) -> None:
    parent = tmp_path / "site-packages"
    parent.mkdir()
    (parent / "release-packages.yaml").write_text("")
    assert _load_layout(parent / "trw-mcp")["MONOREPO_ROOT"] is None


def _monorepo_skip(call: ast.AST) -> bool:
    return (
        isinstance(call, ast.Call)
        and (any(kw.arg == "allow_module_level" for kw in call.keywords) or ast.unparse(call.func).endswith("skipif"))
        and "monorepo" in ast.unparse(call).lower()
    )


def _mirror_skip_conditions(source: str) -> list[tuple[int, str]]:
    """Each module-wide monorepo skip in *source* with its condition: a module-level ``if`` around
    ``pytest.skip(..., allow_module_level=True)``, or a ``pytestmark`` skipif (bare or in a list)."""
    found: list[tuple[int, str]] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.If):
            if any(_monorepo_skip(call) for branch in node.orelse for call in ast.walk(branch)):
                found.append((node.lineno, f"else of {ast.unparse(node.test)}"))  # skipped when the test is false
            elif any(_monorepo_skip(call) for branch in node.body for call in ast.walk(branch)):
                found.append((node.lineno, ast.unparse(node.test)))
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            for call in ast.walk(node.value):
                if _monorepo_skip(call) and isinstance(call, ast.Call):
                    condition = (
                        call.args[0] if call.args else next(kw.value for kw in call.keywords if kw.arg == "condition")
                    )
                    found.append((node.lineno, ast.unparse(condition)))
    return found


def _hand_rolled(source: str) -> list[str]:
    return [
        f"{line}: {condition}"
        for line, condition in _mirror_skip_conditions(source)
        if condition != "MONOREPO_ROOT is None"
    ]


def test_module_level_mirror_skips_use_the_one_monorepo_identity() -> None:
    """A mirror skip keyed on a monorepo file (``scripts/``, a script, a doc) fires silently inside the monorepo
    the day that file moves; keyed on ``MONOREPO_ROOT is None`` (or ``requires_monorepo``), the monorepo always
    runs the module and a missing file fails there (skip audit, SKIPPED-TESTS-trw-mcp.md §4)."""
    offenders = [
        f"{path.relative_to(PACKAGE_ROOT)}:{finding}"
        for path in sorted((PACKAGE_ROOT / "tests").rglob("*.py"))
        for finding in _hand_rolled(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "use `if MONOREPO_ROOT is None:` or `pytestmark = requires_monorepo`:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize(
    "source",
    [
        'if not (ROOT / "scripts").is_dir():\n    pytest.skip("monorepo-only", allow_module_level=True)\n',
        'if MONOREPO_ROOT is MONOREPO_ROOT:\n    pytest.skip("monorepo-only", allow_module_level=True)\n',
        'if MONOREPO_ROOT is None or X:\n    pytest.skip("monorepo-only", allow_module_level=True)\n',
        'pytestmark = pytest.mark.skipif(not DOC.is_file(), reason="monorepo-only claims")\n',
        'pytestmark = [pytest.mark.unit, pytest.mark.skipif(not DOC.is_file(), reason="monorepo-only")]\n',
        'if MONOREPO_ROOT is None:\n    pass\nelse:\n    pytest.skip("monorepo-only", allow_module_level=True)\n',
    ],
    ids=["probe", "tautology", "compound", "pytestmark", "pytestmark-list", "else-branch"],
)
def test_the_census_catches_a_hand_rolled_mirror_skip(source: str) -> None:
    """Guard the guard: every hand-rolled shape is an offender."""
    assert _hand_rolled(source)


def test_the_census_accepts_the_one_identity_and_sees_a_known_module() -> None:
    assert not _hand_rolled('if MONOREPO_ROOT is None:\n    pytest.skip("monorepo-only", allow_module_level=True)\n')
    known = (PACKAGE_ROOT / "tests" / "test_agent_loc.py").read_text(encoding="utf-8")
    assert [condition for _, condition in _mirror_skip_conditions(known)] == ["MONOREPO_ROOT is None"]


def test_this_monorepo_checkout_is_recognized() -> None:
    """Inside the monorepo the mirror skips must never fire; in the public mirror there is nothing to check."""
    if (PACKAGE_ROOT.parent / "release-packages.yaml").is_file():
        assert MONOREPO_ROOT == PACKAGE_ROOT.parent


def _load_layout(package: Path) -> dict:
    tests = package / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    target = tests / "_layout.py"
    target.write_text(Path(__file__).with_name("_layout.py").read_text())
    return runpy.run_path(str(target))
