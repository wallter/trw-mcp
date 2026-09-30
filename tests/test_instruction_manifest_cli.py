"""CLI tests for instruction manifest checking commands."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import _check_instructions_core, _run_check_instructions


def _run(target: Path) -> int:
    with pytest.raises(SystemExit) as exc_info:
        _run_check_instructions(argparse.Namespace(target_dir=str(target)))
    return int(exc_info.value.code or 0)


def _write(root: Path, relpath: str, text: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No machine config, no flag env: the project's own files decide the surface."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("TRW_DISPATCH_TOOLS_EXPOSED", "TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED", "TRW_SURFACE_ROLE"):
        monkeypatch.delenv(var, raising=False)


class TestCheckInstructionsCLI:
    """_run_check_instructions CLI handler produces correct exit codes."""

    def test_clean_exit_code_zero(self, tmp_path: Path) -> None:
        _write(tmp_path, "AGENTS.md", "Use trw_session_start() and trw_learn().\n")
        assert _run(tmp_path) == 0

    def test_mismatch_exit_code_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "AGENTS.md", "Use trw_dispatch() for validation.\n")
        assert _run(tmp_path) == 1

    def test_no_instruction_files_exit_zero_and_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / ".trw").mkdir()  # a TRW project with no carriers yet
        assert _run(tmp_path) == 0
        assert "no instruction files found" in capsys.readouterr().out

    def test_outside_a_project_with_nothing_to_check_is_not_ok(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """INC-126 (c): 'OK: no instruction files found' rc 0 in a non-project dir was a CI false pass."""
        assert _run(tmp_path) == 2
        out = capsys.readouterr()
        assert "not a TRW project" in out.err and "nothing was checked" in out.err and "OK" not in out.out

    def test_a_carrier_symlinked_out_of_the_target_is_a_named_skip_not_ok(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """INC-126 (c): a TRW carrier skipped for resolving outside the target was reported as OK, rc 0."""
        project = tmp_path / "proj"
        (project / ".trw").mkdir(parents=True)
        _write(project, "CLAUDE.md", "Use trw_session_start().\n")
        outside = tmp_path / "elsewhere.md"
        outside.write_text("Call trw_dispatch.\n", encoding="utf-8")
        (project / "AGENTS.md").symlink_to(outside)
        assert _run(project) == 1
        out = capsys.readouterr().out
        assert "AGENTS.md: SKIPPED" in out and "outside the target" in out and "OK" not in out

    def test_a_file_given_as_the_target_is_named_as_a_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write(tmp_path, "AGENTS.md", "x\n")
        assert _run(tmp_path / "AGENTS.md") == 2
        err = capsys.readouterr().err
        assert "is a file, not a directory" in err and "does not exist" not in err

    def test_missing_target_is_an_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert _run(tmp_path / "nope") == 2
        assert "does not exist" in capsys.readouterr().err

    def test_output_names_the_file_and_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _write(tmp_path, ".trw/INSTRUCTIONS.md", "Call trw_dispatch.\n")
        assert _run(tmp_path) == 1
        assert ".trw/INSTRUCTIONS.md: mentions unexposed tools: trw_dispatch" in capsys.readouterr().out


class TestCheckInstructionsCore:
    """Test the separated core logic directly (no sys.exit)."""

    def test_returns_zero_no_files(self, tmp_path: Path) -> None:
        assert _check_instructions_core(tmp_path) == (0, {})

    def test_returns_two_for_missing_target(self, tmp_path: Path) -> None:
        assert _check_instructions_core(tmp_path / "nope") == (2, {})

    def test_returns_one_on_mismatch(self, tmp_path: Path) -> None:
        _write(tmp_path, "AGENTS.md", "Use trw_dispatch() here.\n")
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {"AGENTS.md": ["trw_dispatch"]}

    @pytest.mark.parametrize(
        "relpath",
        [
            ".trw/INSTRUCTIONS.md",
            "CLAUDE.md",
            ".cursor/rules/trw.mdc",
            ".codex/INSTRUCTIONS.md",
            ".opencode/INSTRUCTIONS.md",
            ".github/copilot-instructions.md",
            ".github/instructions/trw-ceremony.instructions.md",
            ".agents/rules/trw-ceremony.md",
            "ANTIGRAVITY.md",
        ],
    )
    def test_every_registry_instruction_surface_is_scanned(self, tmp_path: Path, relpath: str) -> None:
        _write(tmp_path, relpath, "Call trw_dispatch and trw_assess.\n")
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {relpath: ["trw_assess", "trw_dispatch"]}

    def test_unrelated_files_are_not_scanned(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md", "Call trw_dispatch.\n")
        assert _check_instructions_core(tmp_path) == (0, {})

    @pytest.mark.parametrize(
        ("config", "flagged"),
        [
            ("", ["trw_assess", "trw_dispatch"]),
            ("dispatch_tools_exposed: true\n", ["trw_assess"]),
            ("assess_enabled: true\n", ["trw_dispatch"]),
            ("dispatch_tools_exposed: true\nassess_enabled: true\n", []),
        ],
    )
    def test_judged_against_the_projects_resolved_surface(
        self, tmp_path: Path, config: str, flagged: list[str]
    ) -> None:
        if config:
            _write(tmp_path, ".trw/config.yaml", config)
        _write(tmp_path, "AGENTS.md", "Call trw_dispatch and trw_assess.\n")
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert mismatches.get("AGENTS.md", []) == flagged
        assert exit_code == (1 if flagged else 0)


class TestCheckInstructionsScope:
    """Codex r1 KIs on E2E-CHECK-INSTRUCTIONS: only TRW's own carriers, never outside the target."""

    def test_a_symlinked_carrier_resolving_outside_the_target_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.md"
        outside.write_text("Call trw_dispatch.\n", encoding="utf-8")
        project = tmp_path / "proj"
        project.mkdir()
        (project / "CLAUDE.md").symlink_to(outside)
        # Not read (its trw_dispatch mention is not reported) -- and, INC-126 (c), not an OK either.
        assert _check_instructions_core(project) == (1, {})

    def test_with_a_manifest_only_trw_written_rules_are_scanned(self, tmp_path: Path) -> None:
        _write(tmp_path, ".cursor/rules/trw-ceremony.mdc", "Call trw_dispatch.\n")
        _write(tmp_path, ".cursor/rules/my-own.mdc", "I call trw_assess from my own tooling.\n")
        _write(
            tmp_path,
            ".trw/managed-artifacts.yaml",
            "version: 2\ncontent_hashes:\n  .cursor/rules/trw-ceremony.mdc: " + "a" * 64 + "\n",
        )
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {".cursor/rules/trw-ceremony.mdc": ["trw_dispatch"]}

    @pytest.mark.parametrize(
        "manifest",
        ["version: 2\ncontent_hashes: not-a-map\n", "version: 1\n", "{{{ not yaml", ""],
        ids=["malformed-hashes", "legacy-no-hashes", "unparseable", "empty"],
    )
    def test_an_unreadable_or_legacy_manifest_scans_every_rule(self, tmp_path: Path, manifest: str) -> None:
        """Codex r2 KI: an unreadable manifest is 'ownership unknown', never 'TRW owns nothing'."""
        _write(tmp_path, ".cursor/rules/trw-ceremony.mdc", "Call trw_dispatch.\n")
        _write(tmp_path, ".trw/managed-artifacts.yaml", manifest)
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {".cursor/rules/trw-ceremony.mdc": ["trw_dispatch"]}

    def test_a_manifest_symlinked_from_outside_the_target_is_not_trusted(self, tmp_path: Path) -> None:
        """Codex r2 KI: an outside manifest could otherwise mark a TRW rule 'not ours' and hide it."""
        outside = tmp_path / "outside.yaml"
        outside.write_text("version: 2\ncontent_hashes:\n  .cursor/rules/other.mdc: " + "a" * 64 + "\n")
        project = tmp_path / "proj"
        _write(project, ".cursor/rules/trw-ceremony.mdc", "Call trw_dispatch.\n")
        (project / ".trw").mkdir(parents=True, exist_ok=True)
        (project / ".trw" / "managed-artifacts.yaml").symlink_to(outside)
        exit_code, mismatches = _check_instructions_core(project)
        assert exit_code == 1
        assert mismatches == {".cursor/rules/trw-ceremony.mdc": ["trw_dispatch"]}

    def test_a_manifest_symlink_loop_scans_every_rule_instead_of_aborting(self, tmp_path: Path) -> None:
        """Codex r3 KI: resolve() on a loop raises; that is 'ownership unknown', not a crash."""
        _write(tmp_path, ".cursor/rules/trw-ceremony.mdc", "Call trw_dispatch.\n")
        trw = tmp_path / ".trw"
        trw.mkdir(parents=True, exist_ok=True)
        (trw / "loop-a").symlink_to(trw / "loop-b")
        (trw / "loop-b").symlink_to(trw / "loop-a")
        (trw / "managed-artifacts.yaml").symlink_to(trw / "loop-a")
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {".cursor/rules/trw-ceremony.mdc": ["trw_dispatch"]}

    def test_a_looping_carrier_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        _write(tmp_path, "AGENTS.md", "Call trw_dispatch.\n")
        (tmp_path / "loop-x").symlink_to(tmp_path / "loop-y")
        (tmp_path / "loop-y").symlink_to(tmp_path / "loop-x")
        (tmp_path / "CLAUDE.md").symlink_to(tmp_path / "loop-x")
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {"AGENTS.md": ["trw_dispatch"]}

    @pytest.mark.parametrize("error", [RuntimeError, OSError])
    def test_resolve_raising_on_a_loop_is_ownership_unknown_on_every_supported_python(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
    ) -> None:
        """Python 3.11/3.12 raise RuntimeError on a symlink loop (3.13+ do not), so simulate it: the check must
        scan every rule and skip the looping carrier rather than abort."""
        _write(tmp_path, ".cursor/rules/trw-ceremony.mdc", "Call trw_dispatch.\n")
        _write(
            tmp_path, ".trw/managed-artifacts.yaml", "version: 2\ncontent_hashes:\n  .cursor/rules/x.mdc: " + "a" * 64
        )
        _write(tmp_path, "CLAUDE.md", "Call trw_assess.\n")
        real = Path.resolve

        def resolve(self: Path, *args: object, **kwargs: object) -> Path:
            if self.name in {"managed-artifacts.yaml", "CLAUDE.md"}:
                raise error("Symlink loop from " + str(self))
            return real(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "resolve", resolve)
        exit_code, mismatches = _check_instructions_core(tmp_path)
        assert exit_code == 1
        assert mismatches == {".cursor/rules/trw-ceremony.mdc": ["trw_dispatch"]}


def test_an_unreadable_manifest_is_explained_in_words(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, ".trw/managed-artifacts.yaml", "version: [\n")
    _write(tmp_path, "AGENTS.md", "Project notes.\n")
    assert _run(tmp_path) == 0
    assert "ownership is unknown and every rule file was scanned" in capsys.readouterr().err


def test_a_carrier_name_with_terminal_controls_is_printed_escaped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """codex r1 KI (INC-126): a skipped carrier's name reached the terminal raw, escape sequences included."""
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    outside = tmp_path / "elsewhere.mdc"
    outside.write_text("x\n", encoding="utf-8")
    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "evil\x1b[31mred.mdc").symlink_to(outside)
    assert _run(project) == 1
    out = capsys.readouterr().out
    assert "\x1b" not in out and "evil\\x1b[31mred.mdc: SKIPPED" in out
