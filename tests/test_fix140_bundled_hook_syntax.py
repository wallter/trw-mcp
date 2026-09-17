"""PRD-FIX-140-FR09 — every shipped hook parses with the shell it declares.

``make bundle-sync`` compared bytes between the bundle and its client projections
and never parsed a script, so ``.claude/hooks/self-review.sh`` shipped a bash 3.2
parse error (a nested ``case`` inside ``$( ... )`` inside an enclosing ``case``
branch) while the sync check stayed green. These tests exercise the same helper
the gate now runs, plus a deliberately broken fixture so the check cannot pass by
never failing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_HOOKS = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "hooks"
_GATE = _REPO_ROOT / "scripts" / "check-bundle-sync.sh"


def _declared_shell(script: Path) -> str:
    shebang = script.read_text(encoding="utf-8").splitlines()[0]
    if not shebang.startswith("#!"):
        return "sh"
    parts = shebang[2:].split()
    interpreter = parts[0]
    if interpreter.endswith("/env") and len(parts) > 1:
        interpreter = parts[1]
    return interpreter


def _parses(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([_declared_shell(script), "-n", str(script)], capture_output=True, text=True, check=False)


class TestEveryBundledHookParses:
    """The bundle, the gate's own helper, and the repaired repo-local script."""

    def test_the_bundle_is_not_empty(self) -> None:
        """Guards the parametrized scan below against a vacuous pass.

        The floor is a literal on purpose: deriving it from the bundle it is
        meant to police would make the guard tautological. It must stay on ONE
        line with its marker -- scripts/census/_suppress.py keys a suppression
        to the marker's LINE NUMBER, so wrapping this assert (as a formatter
        once did) leaves the literal on line N and the marker on line N+1, and
        the census gate reports it as unsuppressed.
        """
        assert len(list(_BUNDLED_HOOKS.rglob("*.sh"))) > 10  # census-ok: vacuity floor, not a count claim

    @pytest.mark.parametrize("script", sorted(_BUNDLED_HOOKS.rglob("*.sh")), ids=lambda p: p.name)
    def test_every_bundled_hook_parses(self, script: Path) -> None:
        result = _parses(script)

        assert result.returncode == 0, f"{script.name}: {result.stderr.strip()}"

    def test_the_repaired_repo_local_self_review_hook_parses(self) -> None:
        """It was dead on arrival under bash 3.2 and no gate could see it."""
        script = _REPO_ROOT / ".claude" / "hooks" / "self-review.sh"
        assert script.is_file()

        for shell in ("sh", "bash"):
            result = subprocess.run([shell, "-n", str(script)], capture_output=True, text=True, check=False)
            assert result.returncode == 0, f"{shell}: {result.stderr.strip()}"

    def test_the_gate_runs_the_syntax_pass(self) -> None:
        body = _GATE.read_text(encoding="utf-8")

        assert "check_hook_syntax" in body
        assert "$BUNDLED_BASE/hooks" in body

    def test_a_broken_script_is_rejected(self, tmp_path: Path) -> None:
        """The exact shape that shipped: a nested case inside $( ) inside a case."""
        broken = tmp_path / "broken.sh"
        broken.write_text(
            "#!/bin/sh\n"
            'case "x" in\n'
            "  *.py)\n"
            "    v=$(echo a | while read -r l; do\n"
            '      case "$l" in\n'
            '        "a"*) echo hit ;;\n'
            "      esac\n"
            "    done) || true\n"
            '    echo "$v"\n'
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )

        result = subprocess.run(["bash", "-n", str(broken)], capture_output=True, text=True, check=False)

        assert result.returncode != 0, "the syntax check would not have caught the shipped defect"
