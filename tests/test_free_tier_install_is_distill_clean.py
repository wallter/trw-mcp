"""PRD-CORE-239 FR03 — the free tier is unchanged, and provably so.

The operator constraint has two halves: TRW must work completely WITHOUT the
proprietary `trw-distill`, and must exploit it fully WITH it. This file pins the
first half for every profile in `docs/CLIENT-PROFILES.md`.

The defect it guards against is not hypothetical. Before `ed15310966` the
install path had no availability check anywhere — a repo-wide grep for
`distill_installed` / `find_spec("trw_distill")` across `bootstrap/` and
`channels/` returned zero hits — so every client planted distill-dependent
artifacts regardless of licence: Copilot instructions telling the user to run
`trw-distill self-improve risk-report` (which yields `command not found`),
Cursor `.mdc` stubs claiming "TRW distill data available — quota exceeded", and
three copies of an explorer subagent that cannot function without the package.

Why a written-file scan rather than a list of known-bad artifacts: the original
defect was an entire subsystem with no gate, so a test naming the four files
that were fixed would pass while a fifth was added. This walks everything
`init-project` actually wrote.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project

#: The seven built-in profiles from `docs/CLIENT-PROFILES.md`. `aider` is
#: retired (uninstall/migration only) and `gemini` was removed 2026-07-24.
PROFILES = (
    "antigravity-cli",
    "claude-code",
    "codex",
    "copilot",
    "cursor-cli",
    "cursor-ide",
    "opencode",
)

#: The predicate is IMPERATIVE INVOCATION, not bare mention — the same
#: distinction `test_distill_entitlement_gate.py` draws, and for the same
#: reason: naming the package is not the defect, telling an unlicensed user to
#: execute it is.
#:
#: Running the coarse version first was worth it, because it showed exactly
#: what a clean free-tier install legitimately contains and none of it is the
#: defect class:
#:   - `.claude/skills/trw-memory-{audit,optimize}/SKILL.md` say "If the
#:     OPTIONAL `trw-distill` package is installed …" — conditional and honest,
#:     which is precisely how a paid package should be referenced.
#:   - `.opencode/commands/trw-distill-{conventions,hotspots}.md` carry the
#:     string only in the filename; their bodies call `trw_recall` and
#:     `trw_codebase_risk_report` over MCP (PRD-CORE-239 §3b).
#:   - `.trw/channels/manifest.yaml` and `.trw/managed-artifacts.yaml` record
#:     those filenames as internal bookkeeping, not instruction.
#:   - `.trw/hooks/trw-post-commit.sh` comments that it exits 0 "including
#:     missing Python and missing trw-distill" — a guarantee, not an advert.
#: Widening an allowlist to cover those would have been suppressing the signal;
#: sharpening the predicate keeps it.
#: Verbs match the entitlement guard's proven set. `invoke` was tried and
#: dropped: it fired on `.claude/skills/trw-memory-optimize/SKILL.md`'s "Do NOT
#: invoke `trw-distill maintain optimize --apply`", a prohibition — the exact
#: opposite of the defect. A negation-aware predicate would be more precise but
#: also more to get wrong; the narrower verb set is honest about its limits.
_IMPERATIVE = re.compile(r"(?:run|regenerate)\W{0,6}trw-distill\s+\w", re.IGNORECASE)

#: A Python import of the proprietary package inside a generated artifact is
#: always wrong, with one exception: the telemetry hooks' own headers state
#: "no trw_distill imports permitted", which is a guarantee about themselves.
_IMPORT = re.compile(r"(?:^|\W)(?:import|from)\s+trw_distill\b")

#: Binary/DB artifacts that are not text and cannot carry instructions.
_SKIP_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".png", ".jpg", ".ico")


def _git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _offending_files(root: Path) -> dict[str, list[str]]:
    """Every written file containing forbidden text, with the matching lines."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in _SKIP_SUFFIXES:
            continue
        if ".git/" in path.as_posix():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = [line.strip() for line in text.splitlines() if _IMPERATIVE.search(line) or _IMPORT.search(line)]
        if hits:
            found[str(path.relative_to(root))] = hits[:3]
    return found


@pytest.mark.parametrize("profile", PROFILES)
def test_unlicensed_init_project_writes_no_distill_text(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No profile may write `trw-distill` text into an unlicensed project.

    The root conftest already pins `distill_installed` False, but this asserts
    it explicitly rather than depending on a fixture two files away — the
    subject here IS the unlicensed path.
    """
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)
    repo = tmp_path / profile
    _git_repo(repo)

    result = init_project(repo, ide=profile)

    assert not result.get("errors"), (
        f"{profile}: init-project must complete cleanly without trw-distill; errors: {result.get('errors')}"
    )
    offenders = _offending_files(repo)
    assert not offenders, (
        f"{profile}: an unlicensed project was given text naming the paid "
        f"package. Each entry is file -> first matching lines: {offenders}"
    )


@pytest.mark.parametrize("profile", PROFILES)
def test_unlicensed_init_project_writes_no_explorer_subagent(
    profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three explorer agents are gated, not deleted — verify the gate holds.

    Separate from the text scan because an agent file could in principle be
    written without naming the package and still be useless without it.
    """
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)
    repo = tmp_path / profile
    _git_repo(repo)

    init_project(repo, ide=profile)

    planted = [str(p.relative_to(repo)) for p in repo.rglob("*distill-explorer*") if p.is_file()]
    assert not planted, (
        f"{profile}: an unlicensed project received an explorer subagent that "
        f"cannot function without the proprietary package: {planted}"
    )


def test_the_scan_would_actually_catch_something() -> None:
    """The scanner must not pass by being blind.

    Every assertion above is a negative. If `_offending_files` silently matched
    nothing — a bad suffix filter, an over-broad allowlist — all fourteen cases
    would go green while the defect shipped. This plants a known offender and
    requires it to be found.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "AGENTS.md").write_text("Run: trw-distill self-improve risk-report\n", encoding="utf-8")

        found = _offending_files(root)

        assert "AGENTS.md" in found, "the scanner cannot see a planted offender"
