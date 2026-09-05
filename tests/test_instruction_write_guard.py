"""PRD-FIX-123: instruction sync must never destroy user-authored content.

Every test here exercises the REAL production writer against real files on
disk. Nothing about the writer is mocked — the only patched objects are the
project-root resolver (so a temp directory stands in for a repo) and, in two
negative tests, the filesystem permissions used to inject a failure.

Baseline the fix inverts, measured on 2026-09-03 against trw-mcp 1.0.5:

===========================================  =====================
Scenario                                     Pre-fix outcome
===========================================  =====================
322 hand-written lines, 104-line section,
``max_auto_lines=300``                       128 user lines lost
same write, byte accounting                  7618 -> 13417 bytes
                                             (the file GREW)
200-line file, marker-less section,
``max_lines=30``                             170 lost + the TRW
                                             section itself dropped
``generate_cursor_cli_agents_md(force=True)``
on a 322-line file                           322 of 322 lost,
                                             returned ``created``
===========================================  =====================
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401 -- pytest fixture
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import (
    TRW_MARKER_END,
    TRW_MARKER_START,
    merge_trw_section,
)
from trw_mcp.state.claude_md._write_guard import (
    guarded_instruction_write,
    instruction_write_trigger,
    non_generated_bytes,
)

_MARKERS = (TRW_MARKER_START, TRW_MARKER_END)
_AUTO_COMMENT = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

#: The scar line a pre-fix release left behind. Assembled the same way the
#: guard assembles it, so the FR01 grep-absent assertion stays honest.
_SCAR = "<!-- trw: user content " + "truncated to line limit -->"

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"


def _rendered_section() -> str:
    """Return the section the sync actually renders for AGENTS.md (104 lines)."""
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

    return f"{_AUTO_COMMENT}\n{TRW_MARKER_START}\n\n{render_agents_trw_section()}\n{TRW_MARKER_END}\n"


def _handwritten(lines: int) -> str:
    return "\n".join(f"# Hand-written rule {i}" for i in range(lines)) + "\n"


def _padded_block_file(pad_bytes: int) -> str:
    """A marked file whose USER region is one line plus *pad_bytes* of blank lines.

    Blank lines are invisible to :func:`non_generated_bytes` by design, so two
    of these with different padding are indistinguishable to the primary floor
    and differ only in raw total size — exactly the gap the secondary floor
    exists to cover.
    """
    padding = "\n" * pad_bytes
    return f"# keep me\n{padding}\n{_AUTO_COMMENT}\n{TRW_MARKER_START}\nBLOCK\n{TRW_MARKER_END}\n"


def _big_block_file(block_body: str) -> str:
    """A marked file whose USER content is fixed and whose BLOCK carries the bulk."""
    return f"# keep me\n\n{_AUTO_COMMENT}\n{TRW_MARKER_START}\n{block_body}\n{TRW_MARKER_END}\n"


def _marked_file(user_above: str, user_below: str, block_body: str = "old block") -> str:
    return f"{user_above}\n\n{_AUTO_COMMENT}\n{TRW_MARKER_START}\n{block_body}\n{TRW_MARKER_END}\n\n{user_below}\n"


class TestOverflowIsRefused:
    """FR01: the user-content truncation path is gone; overflow is refused."""

    def test_oversized_merge_refuses_and_leaves_file_byte_identical(self, tmp_path: Path) -> None:
        """The reported incident: 322 hand-written lines + a 104-line section at limit 300.

        Pre-fix this destroyed 128 lines and reported success. Now: no write.
        """
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")
        before = target.read_bytes()

        verdict = merge_trw_section(target, _rendered_section(), 300, project_root=tmp_path)

        assert verdict.written is False
        assert target.read_bytes() == before
        refusal = verdict.refusal
        assert refusal is not None
        assert refusal["error_code"] == "instruction_surface_oversized"
        assert refusal["reason"] == "oversized"
        assert refusal["file"] == str(target)
        assert refusal["limit"] == 300
        # Merged line count, measured on this fixture; the point is that the
        # gate now sees the MERGED total rather than the 104-line section.
        # 428, not 429: PRD-CORE-243-FR06/FR08's render_merged_content fix
        # removed a redundant blank line this no-markers-found branch used to
        # add at EOF (trw_section already carries its own trailing newline).
        assert refusal["lines"] == 428
        assert refusal["lines"] > refusal["limit"]
        # Every single hand-written line survives.
        surviving = target.read_text(encoding="utf-8")
        assert all(f"# Hand-written rule {i}" in surviving for i in range(322))

    def test_raising_the_limit_lets_every_hand_written_line_through(self, tmp_path: Path) -> None:
        """US-001 second criterion: above the limit the write proceeds, losing nothing."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")

        verdict = merge_trw_section(target, _rendered_section(), 1000, project_root=tmp_path)

        assert verdict.written is True
        content = target.read_text(encoding="utf-8")
        assert all(f"# Hand-written rule {i}" in content for i in range(322))
        assert TRW_MARKER_START in content

    def test_no_marker_fallback_no_longer_drops_user_lines_or_the_section(self, tmp_path: Path) -> None:
        """Regression: 200 lines + a marker-less section at max_lines=30.

        Pre-fix this kept 30 of 200 user lines AND dropped the TRW section it
        was writing — the write reported success and delivered neither side.
        """
        target = tmp_path / "CLAUDE.md"
        target.write_text("\n".join(f"# Line {i}" for i in range(200)) + "\n", encoding="utf-8")
        before = target.read_bytes()

        verdict = merge_trw_section(target, "\n## New\n- item\n", 30, project_root=tmp_path)

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "oversized"
        assert target.read_bytes() == before
        assert "# Line 199" in target.read_text(encoding="utf-8")

    def test_a_merge_within_the_limit_behaves_exactly_as_before(self, tmp_path: Path) -> None:
        """Control: the guard is not a blanket refusal — normal merges still land."""
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Short file\n\nSome content.\n", encoding="utf-8")

        verdict = merge_trw_section(
            target, f"\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n", 500, project_root=tmp_path
        )

        assert verdict.written is True
        content = target.read_text(encoding="utf-8")
        assert "Some content." in content
        assert TRW_MARKER_START in content and TRW_MARKER_END in content

    def test_prior_truncation_scar_is_reported(
        self, tmp_path: Path, captured_structlog: list[dict[str, object]]
    ) -> None:
        """A file already damaged by a prior release is reported, not silently rewritten."""
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"# Rules\n{_SCAR}\n", encoding="utf-8")

        merge_trw_section(
            target, f"\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n", 500, project_root=tmp_path
        )

        scars = [entry for entry in captured_structlog if entry["event"] == "instruction_prior_truncation_detected"]
        assert len(scars) == 1
        assert scars[0]["path"] == str(target)

    def test_truncation_helper_is_absent_from_the_shipped_source(self) -> None:
        """FR01 assertion: the truncating code and its scar literal are gone."""
        sources = [p.read_text(encoding="utf-8") for p in _SRC_ROOT.rglob("*.py")]
        assert not [s for s in sources if "_truncate_with_markers" in s]
        assert not [s for s in sources if "user content truncated to line limit" in s]


class TestShrinkFloor:
    """FR02: a floor on NON-generated bytes, plus a typed total-shrink floor."""

    def test_growing_file_that_destroys_user_bytes_is_refused(self, tmp_path: Path) -> None:
        """The load-bearing case: the candidate GROWS the file while losing user content.

        A naive total-shrink floor cannot see this — the assertion on the reason
        string is what proves the floor measures the right quantity.
        """
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")
        current_total = len(target.read_bytes())

        # Reconstruct the pre-fix truncated output: first 194 user lines, then a
        # scar, then the full generated block. Bigger overall, 128 lines poorer.
        kept = "\n".join(f"# Hand-written rule {i}" for i in range(194))
        candidate = f"{kept}\n{_SCAR}\n{_rendered_section()}"
        assert len(candidate.encode("utf-8")) > current_total, "fixture must GROW the file"

        verdict = guarded_instruction_write(target, candidate, markers=_MARKERS, project_root=tmp_path)

        assert verdict.written is False
        refusal = verdict.refusal
        assert refusal is not None
        assert refusal["reason"] == "non_generated_shrink"
        assert refusal["candidate_non_generated_bytes"] < refusal["current_non_generated_bytes"]
        assert refusal["candidate_total_bytes"] > refusal["current_total_bytes"]

    def test_force_writes_and_logs_both_byte_deltas(
        self, tmp_path: Path, captured_structlog: list[dict[str, object]]
    ) -> None:
        """FR02: ``force`` is the only bypass, and it is loud."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")
        candidate = _rendered_section()

        verdict = guarded_instruction_write(target, candidate, markers=_MARKERS, force=True, project_root=tmp_path)

        assert verdict.written is True
        forced = [entry for entry in captured_structlog if entry["event"] == "instruction_write_forced"]
        assert len(forced) == 1
        assert forced[0]["non_generated_byte_delta"] < 0
        assert "total_byte_delta" in forced[0]

    def test_marker_bearing_candidate_whose_block_delta_does_not_explain_the_drop_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The exemption is quantitative — carrying markers is not a free pass.

        This is the primary floor's blind spot, made concrete: the file loses
        4,000 raw bytes of blank padding from OUTSIDE the markers, which the
        whitespace-normalised non-generated measurement cannot see (both sides
        normalise to ``# keep me``). The candidate carries a well-formed block,
        but that block did not shrink, so nothing accounts for the missing bytes
        and the total floor is what catches it.
        """
        target = tmp_path / "AGENTS.md"
        target.write_text(_padded_block_file(4000), encoding="utf-8")
        candidate = _padded_block_file(0)
        assert non_generated_bytes(candidate, _MARKERS) == non_generated_bytes(
            target.read_text(encoding="utf-8"), _MARKERS
        ), "fixture must be invisible to the primary floor"

        verdict = guarded_instruction_write(
            target,
            candidate,
            markers=_MARKERS,
            project_root=tmp_path,
            config=TRWConfig(instruction_write_max_total_shrink_fraction=0.25),
        )

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "total_shrink"
        assert target.read_text(encoding="utf-8") == _padded_block_file(4000)

    def test_total_shrink_floor_uses_the_configured_fraction(self, tmp_path: Path) -> None:
        """The floor is the configured number, not a hard-coded one."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_padded_block_file(4000), encoding="utf-8")

        # 0.99 puts the floor at ~1% of the current total, which the candidate
        # clears; 0.25 (previous test) does not.
        verdict = guarded_instruction_write(
            target,
            _padded_block_file(0),
            markers=_MARKERS,
            project_root=tmp_path,
            config=TRWConfig(instruction_write_max_total_shrink_fraction=0.99),
        )

        assert verdict.written is True, verdict.refusal

    def test_a_shrink_the_block_itself_explains_is_admitted(self, tmp_path: Path) -> None:
        """Control: when the block IS what shrank, the drop is accounted for.

        Without this the previous test could pass by refusing everything that
        collapses a file, which would break PRD-CORE-203 externalization.
        """
        target = tmp_path / "AGENTS.md"
        target.write_text(_big_block_file("x" * 4000), encoding="utf-8")

        verdict = guarded_instruction_write(
            target,
            _big_block_file("@.trw/INSTRUCTIONS.md"),
            markers=_MARKERS,
            project_root=tmp_path,
            config=TRWConfig(instruction_write_max_total_shrink_fraction=0.25),
        )

        assert verdict.written is True, verdict.refusal

    def test_externalizing_the_block_to_a_sidecar_is_permitted(self, tmp_path: Path) -> None:
        """PRD-CORE-203 replaces a full inline block with a one-line import.

        A total-only floor refuses that, because the file legitimately collapses.
        The shrink is accounted for by TRW's own region, so it is allowed.
        """
        target = tmp_path / "CLAUDE.md"
        target.write_text(_big_block_file("x" * 4000), encoding="utf-8")

        verdict = guarded_instruction_write(
            target, _big_block_file("@.trw/INSTRUCTIONS.md"), markers=_MARKERS, project_root=tmp_path
        )

        assert verdict.written is True, verdict.refusal
        assert "# keep me" in target.read_text(encoding="utf-8")

    def test_removing_only_generated_bytes_is_permitted(self, tmp_path: Path) -> None:
        """The TRW-block strip performed by the AGENTS.md migration must not be refused."""
        from trw_mcp.state.claude_md._agents_md import _strip_trw_section

        target = tmp_path / "AGENTS.md"
        target.write_text(_marked_file("# My rules", "# More rules"), encoding="utf-8")
        stripped, remaining = _strip_trw_section(target.read_text(encoding="utf-8"))
        assert stripped

        verdict = guarded_instruction_write(target, remaining, markers=_MARKERS, project_root=tmp_path)

        assert verdict.written is True, verdict.refusal
        content = target.read_text(encoding="utf-8")
        assert "# My rules" in content and "# More rules" in content

    def test_non_generated_accounting_covers_marked_unmarked_and_trailing(self) -> None:
        """Unit: the quantity the floor measures, on the three file shapes."""
        marked = _marked_file("# above", "# below")
        assert non_generated_bytes(marked, _MARKERS) == len(b"# above\n# below")
        unmarked = "# a\n# b\n"
        assert non_generated_bytes(unmarked, _MARKERS) == len(b"# a\n# b")
        # Whitespace-only lines never count, so the merge's own separator
        # normalisation cannot read as user-content loss.
        assert non_generated_bytes("# a\n\n\n# b\n", _MARKERS) == non_generated_bytes("# a\n# b\n", _MARKERS)

    def test_config_bounds_fail_validation_rather_than_clamping(self) -> None:
        """NFR03: out-of-range tunables raise, they are not silently clamped."""
        with pytest.raises(ValueError):
            TRWConfig(instruction_backup_retention=0)
        with pytest.raises(ValueError):
            TRWConfig(instruction_backup_retention=1001)
        with pytest.raises(ValueError):
            TRWConfig(instruction_write_max_total_shrink_fraction=1.5)


class TestDryRun:
    """FR03: ``dry_run`` returns the unified diff and writes nothing."""

    def test_dry_run_returns_diff_and_writes_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(_marked_file("# above", "# below"), encoding="utf-8")
        before = target.read_bytes()
        before_mtime = target.stat().st_mtime_ns

        verdict = merge_trw_section(
            target,
            f"{_AUTO_COMMENT}\n{TRW_MARKER_START}\nNEW BLOCK\n{TRW_MARKER_END}\n",
            500,
            dry_run=True,
            project_root=tmp_path,
        )

        assert verdict.written is False
        assert verdict.refusal is None
        assert target.read_bytes() == before
        assert target.stat().st_mtime_ns == before_mtime
        diff = verdict.diff
        assert diff is not None
        assert diff["file"] == str(target)
        assert diff["diff"].startswith("--- a/CLAUDE.md")
        assert "+++ b/CLAUDE.md" in diff["diff"]
        assert "@@" in diff["diff"]
        assert "+NEW BLOCK" in diff["diff"]
        assert diff["diff_truncated"] is False

    def test_dry_run_on_a_refused_target_reports_the_refusal_not_a_diff(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")

        verdict = merge_trw_section(target, _rendered_section(), 300, dry_run=True, project_root=tmp_path)

        assert verdict.diff is None
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "oversized"

    def test_diff_payload_is_bounded(self, tmp_path: Path) -> None:
        """NFR03: the DIFF is capped; a file is never truncated."""
        target = tmp_path / "CLAUDE.md"
        target.write_text(_handwritten(400), encoding="utf-8")

        verdict = guarded_instruction_write(
            target,
            _handwritten(400) + "\n".join(f"# extra {i}" for i in range(400)) + "\n",
            markers=_MARKERS,
            dry_run=True,
            project_root=tmp_path,
            config=TRWConfig(instruction_dry_run_diff_max_lines=20),
        )

        diff = verdict.diff
        assert diff is not None
        assert diff["diff_truncated"] is True
        assert diff["diff_line_cap"] == 20
        assert len(diff["diff"].splitlines()) <= 20
        assert target.read_text(encoding="utf-8") == _handwritten(400)

    def test_tool_dry_run_leaves_every_target_byte_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR03 end to end through ``trw_instructions_sync``."""
        result, before = _run_sync_tool(tmp_path, monkeypatch, dry_run=True)

        assert result["status"] == "dry_run"
        assert "diffs" in result
        for path, content in before.items():
            assert path.read_bytes() == content


class TestBackup:
    """FR04: a retained pre-write copy under the configured backup directory."""

    def test_backup_precedes_write_and_retention_prunes(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        config = TRWConfig(instruction_backup_retention=3)
        backups = tmp_path / config.instruction_backup_dir

        first_bytes = target.read_bytes()
        guarded_instruction_write(
            target, "# original\n# added 0\n", markers=_MARKERS, project_root=tmp_path, config=config
        )
        copies = list(backups.glob("AGENTS.md.*"))
        assert len(copies) == 1
        assert copies[0].read_bytes() == first_bytes

        for i in range(1, 6):
            time.sleep(0.001)
            guarded_instruction_write(
                target,
                "# original\n" + "".join(f"# added {j}\n" for j in range(i + 1)),
                markers=_MARKERS,
                project_root=tmp_path,
                config=config,
            )
        assert len(list(backups.glob("AGENTS.md.*"))) == config.instruction_backup_retention

    def test_an_identical_rewrite_takes_no_new_backup(self, tmp_path: Path) -> None:
        """NFR02 idempotence: a second identical sync adds nothing."""
        target = tmp_path / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        backups = tmp_path / TRWConfig().instruction_backup_dir

        guarded_instruction_write(target, "# original\n# added\n", markers=_MARKERS, project_root=tmp_path)
        assert len(list(backups.glob("AGENTS.md.*"))) == 1
        guarded_instruction_write(target, "# original\n# added\n", markers=_MARKERS, project_root=tmp_path)
        assert len(list(backups.glob("AGENTS.md.*"))) == 1

    def test_a_missing_target_creates_without_a_backup(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"

        verdict = guarded_instruction_write(target, "# fresh\n", markers=_MARKERS, project_root=tmp_path)

        assert verdict.written is True
        assert verdict.backup_path is None
        assert not (tmp_path / TRWConfig().instruction_backup_dir).exists()

    def test_bundled_gitignore_and_merge_ensure_carry_the_backups_rule(self, tmp_path: Path) -> None:
        """FR04: fresh init deploys the rule; brownfield merge-ensures it."""
        bundled = (_SRC_ROOT / "data" / "gitignore.txt").read_text(encoding="utf-8")
        assert "backups/" in [line.strip() for line in bundled.splitlines()]

        from trw_mcp.bootstrap._gitignore_merge import _ensure_credentials_gitignored

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        custom = "# my own rules\nscratch/\n"
        (trw_dir / ".gitignore").write_text(custom, encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}

        _ensure_credentials_gitignored(tmp_path, result, dry_run=False)
        merged = (trw_dir / ".gitignore").read_text(encoding="utf-8")

        assert merged.startswith(custom)
        rules = [line.strip() for line in merged.splitlines()]
        assert "backups/" in rules
        assert "credentials.yaml" in rules

        # Idempotent: a second run changes nothing.
        _ensure_credentials_gitignored(tmp_path, result, dry_run=False)
        assert (trw_dir / ".gitignore").read_text(encoding="utf-8") == merged


class TestProvenance:
    """FR05: one ``instruction_write_provenance`` record per written target."""

    def test_every_write_path_logs_its_trigger(
        self, tmp_path: Path, captured_structlog: list[dict[str, object]]
    ) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# base\n", encoding="utf-8")

        grown = "# base\n"
        for trigger, tool in (
            ("tool_call", "trw_instructions_sync"),
            ("bootstrap_init", "init-project"),
            ("bootstrap_update", "update-project"),
        ):
            grown += f"# {tool} line\n"
            with instruction_write_trigger(trigger, tool):  # type: ignore[arg-type]
                guarded_instruction_write(target, grown, markers=_MARKERS, project_root=tmp_path)
        # No trigger declared -> the record is STILL emitted, as `unknown`.
        guarded_instruction_write(target, grown + "# more\n", markers=_MARKERS, project_root=tmp_path)

        records = [entry for entry in captured_structlog if entry["event"] == "instruction_write_provenance"]
        assert [r["trigger"] for r in records] == ["tool_call", "bootstrap_init", "bootstrap_update", "unknown"]
        assert all(r["path"] == str(target) for r in records)
        assert all("byte_delta" in r for r in records)
        assert records[0]["caller_tool"] == "trw_instructions_sync"
        assert records[-1]["caller_tool"] == ""

    def test_a_refused_write_emits_no_provenance(
        self, tmp_path: Path, captured_structlog: list[dict[str, object]]
    ) -> None:
        """Control: provenance means a write happened, not that one was attempted."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")

        merge_trw_section(target, _rendered_section(), 300, project_root=tmp_path)

        assert not [entry for entry in captured_structlog if entry["event"] == "instruction_write_provenance"]

    def test_the_sync_tool_supplies_the_tool_call_trigger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
    ) -> None:
        _run_sync_tool(tmp_path, monkeypatch)

        records = [entry for entry in captured_structlog if entry["event"] == "instruction_write_provenance"]
        assert records, "the sync wrote nothing"
        assert {r["trigger"] for r in records} == {"tool_call"}
        assert {r["caller_tool"] for r in records} == {"trw_instructions_sync"}

    def test_bootstrap_entry_points_declare_their_own_triggers(self) -> None:
        """FR05: ``init_project`` / ``update_project`` carry the bootstrap triggers."""
        for module, trigger in (
            ("_init_project.py", "bootstrap_init"),
            ("_update_project.py", "bootstrap_update"),
        ):
            source = (_SRC_ROOT / "bootstrap" / module).read_text(encoding="utf-8")
            assert f'with_instruction_write_trigger("{trigger}"' in source


def _decorated_entry_points() -> list[tuple[Path, str]]:
    """Every function ``@with_instruction_write_trigger(...)`` decorates.

    Grep-enumerated (not hardcoded) so a new call site is covered
    automatically instead of silently escaping this test.
    """
    sites: list[tuple[Path, str]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "@with_instruction_write_trigger(" not in line:
                continue
            for follower in lines[i + 1 :]:
                stripped = follower.strip()
                if not stripped or stripped.startswith("@"):
                    continue
                assert stripped.startswith("def "), f"{path}:{i + 1}: decorator not followed by a def"
                sites.append((path, stripped.removeprefix("def ").split("(", 1)[0]))
                break
    return sites


class TestDecoratorPreservesIntrospection:
    """``with_instruction_write_trigger`` must be introspection-transparent.

    A plain closure wrapper without care taken here reports its OWN module as
    the entry point's home — the guard module, not ``init_project`` /
    ``update_project`` — which breaks anything that introspects "where is this
    defined" (doctor checks, tracebacks, ``inspect.getsource``).
    """

    def test_decorated_entry_points_exist(self) -> None:
        """Control: the enumeration itself must not be vacuous."""
        assert len(_decorated_entry_points()) >= 2

    def test_name_module_doc_and_wrapped_are_preserved(self) -> None:
        import importlib
        import inspect

        for path, func_name in _decorated_entry_points():
            module_name = "trw_mcp." + ".".join(path.relative_to(_SRC_ROOT.parent).with_suffix("").parts[1:]).replace(
                ".__init__", ""
            )
            module = importlib.import_module(module_name)
            fn = getattr(module, func_name)

            assert fn.__name__ == func_name
            assert fn.__module__ == module_name
            assert fn.__doc__, f"{module_name}.{func_name} lost its docstring"
            assert inspect.unwrap(fn).__name__ == func_name

    def test_getfile_resolves_to_the_real_definition_not_the_guard(self) -> None:
        """The regression this guards: getfile() does not follow ``__wrapped__``,
        so the wrapper's own ``__code__`` must be rebound to the wrapped
        function's file/line -- functools.wraps alone does not do this."""
        import importlib
        import inspect

        for path, func_name in _decorated_entry_points():
            module_name = "trw_mcp." + ".".join(path.relative_to(_SRC_ROOT.parent).with_suffix("").parts[1:]).replace(
                ".__init__", ""
            )
            module = importlib.import_module(module_name)
            fn = getattr(module, func_name)

            resolved = Path(inspect.getfile(fn)).resolve()
            assert resolved == path.resolve(), f"{module_name}.{func_name}: getfile() points at {resolved}"
            assert "_write_guard.py" not in str(resolved)


class TestWriterTotality:
    """FR06: every instruction-file writer routes through the one guard.

    RISK-002 in the PRD is explicit that a hand-maintained writer list is the
    weak form of this test: "enumerate by scanning for writes to a repo-root
    CLAUDE.md/AGENTS.md rather than by a hand-maintained list". The scan below
    is that enumeration. It found a writer the PRD's own nine-site table missed
    (`bootstrap/_template_updater.py`, two CLAUDE.md scaffold writes) and the
    dead-but-loaded `_migrate_trw_content_from_agents_md` strip.
    """

    #: The only sanctioned ways to reach the guard.
    GUARD_SYMBOLS = frozenset({"guarded_instruction_write", "guarded_bootstrap_write"})

    #: Sites the widened (call-site-following) scan flags that are permitted to
    #: write unguarded. Each entry needs its own justification below -- an
    #: entry here is a module that can touch a user's instruction file without
    #: the totality invariant applying to it, so "the scan can't prove it" is
    #: not sufficient; the write itself must be provably content-preserving.
    ALLOWLIST: frozenset[str] = frozenset(
        {
            # `_write_if_missing`'s bare `dest` parameter resolves to CLAUDE.md at
            # every call site the scan can see, including this one -- but THIS
            # call only runs in the `else` branch of `claude_md_path.exists() and
            # force` (see `_generate_root_files`), i.e. exactly when the file is
            # either absent (nothing to destroy) or present-and-not-forced
            # (`_write_if_missing` itself skips: `dest.exists() and not force`).
            # The genuinely destructive combination (exists + force) was moved to
            # `guarded_claude_md_scaffold_write` above it in the same function.
            # The scan has no control-flow model, so it cannot see that split.
            #
            # Line number PRD-CORE-262-FR05/CORE262-13/14 drift: 361->371. FR05
            # added a `codex_only` guard above this call (an explicit
            # codex-only selection skips this branch and the whole write
            # entirely, never a new write path); the branch shape and
            # rationale above are otherwise unchanged.
            "bootstrap/_init_project.py:371 (target_dir / 'CLAUDE.md')",
            # `_strip_orphaned_block` only ever removes the TRW-marked region
            # (`_strip_trw_section`) and rewrites `remaining` verbatim -- it is a
            # narrow heal/strip operation, never a content REPLACEMENT, and is
            # structurally identical to `_instruction_carrier.heal_pointer`.
            #
            # CORE262-14 (2026-09-05): the raw `path.write_text()` this used to
            # flag is GONE -- `_strip_orphaned_block` now writes via
            # `FileStateWriter().write_text(path, ...)`, the same shape
            # heal_pointer uses, specifically so a genuine write failure raises
            # `StateError` instead of returning `False` indistinguishably from
            # "nothing to strip" (both callers, `_init_project.py` and
            # `_template_updater.py`, now catch `StateError` and record a
            # `result["errors"]` entry rather than silently reporting a clean
            # result with the foreign block still in place).
            #
            # The two rows below stay allowlisted because `_write_target`/
            # `_is_persistence_writer_receiver` resolve `FileStateWriter().
            # write_text(target, ...)` to `target`, not the receiver (see
            # `test_the_scan_resolves_a_persistence_class_writer_to_its_argument`),
            # so the widened call site is still flagged by name -- routing
            # through `guarded_instruction_write` instead would additionally
            # be a merge-semantics behavior change this fix does not take (a
            # strip-only write is not the same operation `guarded_instruction_write`
            # was built for). heal_pointer itself still never appears in this
            # scan's output: its only caller is `pointer_skip_guard(target)`,
            # itself called with a bare, unresolved `target` parameter, so the
            # surface-shaped argument lives TWO call-site hops away and the
            # widening pass here is documented one-hop-only
            # (`_widen_via_call_sites`); heal_pointer's safety is established
            # directly instead, by `test_instruction_carrier.py::TestHealPointer`.
            "state/claude_md/_orphan_strip.py:190 (project_root / 'AGENTS.md')",
            "state/claude_md/_orphan_strip.py:213 (project_root / 'CLAUDE.md')",
        }
    )

    def test_every_instruction_writer_routes_through_the_guard(self) -> None:
        """Set difference between "writes an instruction surface" and "calls the guard"."""
        unguarded = sorted(
            f"{site.module}:{site.lineno} ({site.target})"
            for site in _scan_instruction_writes(_SRC_ROOT)
            if not (self.GUARD_SYMBOLS & _referenced_names(_SRC_ROOT / site.module))
        )
        assert [u for u in unguarded if u not in self.ALLOWLIST] == []

    def test_no_module_writes_an_instruction_surface_outside_the_seam(self) -> None:
        """No raw write to a user instruction surface survives anywhere under src/.

        Stronger than the per-module check above: a module can call the guard for
        one target and still write another one raw. Every flagged site must be
        inside the seam itself.
        """
        seam = {"state/claude_md/_write_guard.py", "bootstrap/_guarded_write.py"}
        offenders = sorted(
            f"{site.module}:{site.lineno} ({site.target})"
            for site in _scan_instruction_writes(_SRC_ROOT)
            if site.module not in seam
        )
        assert [o for o in offenders if o not in self.ALLOWLIST] == []

    def test_the_scan_detects_an_unguarded_writer(self) -> None:
        """Positive control: measuring nothing is not passing.

        A scanner whose predicate silently stopped matching would make both
        assertions above vacuously true, and nothing else in this file would
        notice. This pins that the scan still fires on the exact shape it exists
        to catch — the bare `agents_path.write_text(...)` that
        `_migrate_trw_content_from_agents_md` carried before FR06 routed it.

        Run against the pre-FR06 sources (commit fb50faa8b3~1) the same scan
        reports 8 sites: 3 in `_opencode.py`, 2 in `_cursor_cli.py`, 2 in
        `_template_updater.py` — which the PRD's own nine-site table did not
        list — and the `_agents_md.py` strip. It reports 0 today.
        """
        planted = (
            "from pathlib import Path\n"
            "def writer(target_dir: Path) -> None:\n"
            '    agents_path = target_dir / "AGENTS.md"\n'
            '    agents_path.write_text("clobbered", encoding="utf-8")\n'
        )
        found = _scan_source_for_instruction_writes(planted, module="planted.py")
        assert [(s.lineno, s.target) for s in found] == [(4, "target_dir / 'AGENTS.md'")]

    def test_the_scan_resolves_a_persistence_class_writer_to_its_argument(self) -> None:
        """Positive control for the ``FileStateWriter().write_text(target, ...)`` shape.

        ``_write_target`` returned the RECEIVER for every ``.write_text``/
        ``.write_bytes`` attribute call, which is correct for
        ``target.write_text(content)`` but wrong when the receiver is a
        ``FileStateWriter``/``FileStateReader`` instance: there the real write
        target is the first positional ARGUMENT, not the receiver. Before this
        fix the scan resolved ``FileStateWriter().write_text(agents_path, ...)``
        to the receiver expression ``FileStateWriter()``, which never names a
        surface, so the write was silently invisible -- the exact shape
        ``state/claude_md/_instruction_carrier.py::heal_pointer`` uses
        (small-fix-4 item 4).

        Covers both the inline-constructor receiver (``FileStateWriter()``) and
        a stored-instance receiver (``writer = FileStateWriter(); writer...``).
        """
        planted = (
            "from pathlib import Path\n"
            "from trw_mcp.state.persistence import FileStateWriter\n"
            "def inline_writer(target_dir: Path) -> None:\n"
            '    agents_path = target_dir / "AGENTS.md"\n'
            '    FileStateWriter().write_text(agents_path, "clobbered")\n'
            "def instance_writer(target_dir: Path) -> None:\n"
            "    writer = FileStateWriter()\n"
            '    claude_md_path = target_dir / "CLAUDE.md"\n'
            '    writer.write_text(claude_md_path, "clobbered")\n'
        )
        found = _scan_source_for_instruction_writes(planted, module="planted.py")
        assert [(s.lineno, s.target) for s in found] == [
            (5, "target_dir / 'AGENTS.md'"),
            (9, "target_dir / 'CLAUDE.md'"),
        ]

    def test_the_widened_scan_detects_a_bare_parameter_writer(self, tmp_path: Path) -> None:
        """Positive control for the FR06 gap this fix closes.

        The exact shape that let `bootstrap/_file_ops.py::_write_if_missing`
        escape detection for a full cycle: a generic ``dest.write_text(...)``
        whose ``dest`` is a bare parameter, called from a SEPARATE module with
        a surface-shaped argument. The direct (non-widened) scan sees neither
        file as a hit on its own; only following the call site closes the gap.
        """
        (tmp_path / "writer_lib.py").write_text(
            "from pathlib import Path\n"
            "def write_if_missing(dest: Path, content: str) -> None:\n"
            "    dest.write_text(content, encoding='utf-8')\n",
            encoding="utf-8",
        )
        (tmp_path / "caller.py").write_text(
            "from pathlib import Path\n"
            "from writer_lib import write_if_missing\n"
            "def scaffold(target_dir: Path) -> None:\n"
            "    claude_md_path = target_dir / 'CLAUDE.md'\n"
            "    write_if_missing(claude_md_path, 'minimal')\n",
            encoding="utf-8",
        )

        direct = [
            s
            for path in sorted(tmp_path.glob("*.py"))
            for s in _scan_source_for_instruction_writes(path.read_text(encoding="utf-8"), module=path.name)
        ]
        assert direct == [], "the write shape must be invisible to the direct (non-widened) scan"

        widened = _widen_via_call_sites(tmp_path, _collect_parametrized_writes(tmp_path))
        assert [(s.module, s.lineno, s.target) for s in widened] == [("caller.py", 5, "target_dir / 'CLAUDE.md'")]

    def test_the_bootstrap_adapter_itself_calls_the_guard(self) -> None:
        """A name-based registry check is blind inside its own member.

        ``guarded_bootstrap_write`` is one of the sanctioned symbols above, so
        the adapter that provides it must be proven to reach the real guard —
        otherwise satisfying the totality test would be a rename away.
        """
        assert "guarded_instruction_write" in _referenced_names(_SRC_ROOT / "bootstrap" / "_guarded_write.py")

    def test_marked_file_keeps_every_byte_outside_the_markers(self, tmp_path: Path) -> None:
        """Mandated scenario 1, on the real writer."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_marked_file("# ABOVE the block", "# BELOW the block"), encoding="utf-8")

        merge_trw_section(
            target, f"{_AUTO_COMMENT}\n{TRW_MARKER_START}\nNEW\n{TRW_MARKER_END}\n", 500, project_root=tmp_path
        )

        content = target.read_text(encoding="utf-8")
        assert "# ABOVE the block" in content
        assert "# BELOW the block" in content
        assert "old block" not in content
        assert "NEW" in content

    def test_unmarked_file_keeps_every_pre_existing_byte(self, tmp_path: Path) -> None:
        """Mandated scenario 2: the block is appended, never substituted."""
        target = tmp_path / "AGENTS.md"
        original = "# My rulebook\n\n- rule one\n- rule two\n"
        target.write_text(original, encoding="utf-8")

        merge_trw_section(
            target, f"{_AUTO_COMMENT}\n{TRW_MARKER_START}\nBLOCK\n{TRW_MARKER_END}\n", 500, project_root=tmp_path
        )

        content = target.read_text(encoding="utf-8")
        for line in original.strip().splitlines():
            assert line in content
        assert "BLOCK" in content

    def test_cursor_cli_force_backs_up_and_no_force_refuses(self, tmp_path: Path) -> None:
        """FR06 + the measured 322-of-322 loss under ``init-project --force``."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_agents_md

        target = tmp_path / "AGENTS.md"
        original = _handwritten(322)
        target.write_text(original, encoding="utf-8")

        forced = generate_cursor_cli_agents_md(tmp_path, "TRW BODY", force=True)

        # ``updated``, not ``created``: pre-fix this branch reported an existing
        # file as ``created`` after replacing it — success-shaped total loss.
        assert forced["updated"] == ["AGENTS.md"]
        assert forced["created"] == []
        backups = list((tmp_path / TRWConfig().instruction_backup_dir).glob("AGENTS.md.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original

        # Without force, the same wholesale replacement is refused outright.
        target.write_text(original, encoding="utf-8")
        (tmp_path / "AGENTS.md").touch()
        unforced = _replace_without_force(tmp_path, target)
        assert unforced is False
        assert target.read_text(encoding="utf-8") == original

    def test_init_project_force_backs_up_claude_md_instead_of_clobbering(self, tmp_path: Path) -> None:
        """PRD-CORE-247 diag-canon finding 3: a bare-parameter write escaped FR06.

        ``_generate_root_files`` scaffolded CLAUDE.md via ``_write_if_missing``,
        whose target is a bare ``dest`` parameter -- invisible to the FR06 scan,
        which only recognizes a write target named after the surface (e.g.
        ``claude_md``) or matching a surface filename in its own unparsed text.
        With ``force=True`` on an existing hand-edited CLAUDE.md this was a raw
        ``write_text`` clobber: no backup, no merge, the same success-shaped
        total loss ``generate_cursor_cli_agents_md`` was fixed for.
        """
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"]

        claude_md = tmp_path / "CLAUDE.md"
        original = claude_md.read_text(encoding="utf-8")
        hand_edit = "\n\n## My private notes\n\nDo not lose this.\n"
        claude_md.write_text(original + hand_edit, encoding="utf-8")

        forced = init_project(tmp_path, ide="claude-code", force=True)
        assert not forced["errors"]

        backups = list((tmp_path / TRWConfig().instruction_backup_dir).glob("CLAUDE.md.*"))
        assert len(backups) >= 1, "a forced rewrite of an existing CLAUDE.md must be backed up, not clobbered"
        assert any("My private notes" in b.read_text(encoding="utf-8") for b in backups)

    def test_no_writer_reports_success_while_losing_user_bytes(self, tmp_path: Path) -> None:
        """NFR04: success and non-generated shrink are mutually exclusive."""
        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(50), encoding="utf-8")
        before = non_generated_bytes(target.read_text(encoding="utf-8"), _MARKERS)

        verdict = guarded_instruction_write(target, "# only this\n", markers=_MARKERS, project_root=tmp_path)
        after = non_generated_bytes(target.read_text(encoding="utf-8"), _MARKERS)

        assert not (verdict.written and after < before)
        assert verdict.written is False
        assert after == before


class TestSizeGateMeasuresMergedTotal:
    """FR07: the gate evaluates the merged total, not the rendered section."""

    def test_gate_sees_merged_total_not_rendered_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import _agents_md as am

        target = tmp_path / "AGENTS.md"
        target.write_text(_handwritten(322), encoding="utf-8")
        seen: list[int] = []
        real_gate = am._enforce_size_gate

        def _spy(file_label: str, lines: int, limit: int, mode: str) -> object:
            seen.append(lines)
            return real_gate(file_label, lines, limit, mode)  # type: ignore[arg-type]

        monkeypatch.setattr(am, "_enforce_size_gate", _spy)
        synced, _path, _verdict = am._sync_agents_md_if_needed(
            True, TRWConfig(max_auto_lines=300), tmp_path, tmp_path / ".trw"
        )

        assert seen, "the gate was never consulted"
        # Pre-fix the gate received the 104-line rendered section and returned
        # None. It now receives the merged total and blocks.
        assert seen[0] > 300
        assert synced is False

    def test_render_side_protection_survives(self, tmp_path: Path) -> None:
        """A rendered section that alone exceeds the limit is still oversize."""
        from trw_mcp.state.claude_md._agents_md_size_gate import enforce_size_gate
        from trw_mcp.state.claude_md._parser import render_merged_content

        target = tmp_path / "AGENTS.md"  # does not exist -> merged == section
        section = f"{TRW_MARKER_START}\n" + "\n".join(f"line {i}" for i in range(400)) + f"\n{TRW_MARKER_END}\n"
        gate_lines = max(section.count("\n"), len(render_merged_content(target, section).split("\n")))

        assert enforce_size_gate("AGENTS.md", gate_lines, 300, "block") is not None


class TestFailClosed:
    """NFR02: every guard failure REFUSES; no path is fail-open."""

    def test_backup_failure_refuses_the_write(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        before = target.read_bytes()
        # A regular FILE where the backup directory must go: mkdir fails.
        blocked = tmp_path / "blocked"
        blocked.write_text("not a directory\n", encoding="utf-8")

        verdict = guarded_instruction_write(
            target,
            "# original\n# more\n",
            markers=_MARKERS,
            project_root=tmp_path,
            config=TRWConfig(instruction_backup_dir="blocked/instructions"),
        )

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "backup_failed"
        assert target.read_bytes() == before

    def test_undecodable_target_refuses_the_write(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_bytes(b"\xff\xfe not utf-8 \xff")
        before = target.read_bytes()

        verdict = guarded_instruction_write(target, "# new\n", markers=_MARKERS, project_root=tmp_path)

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "unreadable_target"
        assert target.read_bytes() == before

    def test_writes_land_atomically(self, tmp_path: Path) -> None:
        """The write goes through FileStateWriter's temp-file-then-rename path."""
        source = (_SRC_ROOT / "state" / "claude_md" / "_write_guard.py").read_text(encoding="utf-8")
        assert "FileStateWriter().write_text" in source
        assert ".write_text(" not in source.replace("FileStateWriter().write_text(", "")


class TestSecurity:
    """NFR03: containment and bounded payloads."""

    def test_backup_path_escape_is_refused(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        outside = tmp_path / "outside"
        target = project / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        before = target.read_bytes()

        verdict = guarded_instruction_write(
            target,
            "# original\n# more\n",
            markers=_MARKERS,
            project_root=project,
            config=TRWConfig(instruction_backup_dir="../outside/backups"),
        )

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "backup_path_escape"
        assert target.read_bytes() == before
        assert not outside.exists()

    def test_provenance_never_logs_file_content(
        self, tmp_path: Path, captured_structlog: list[dict[str, object]]
    ) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        secret = "# api key lives at /etc/secret.token\n"

        guarded_instruction_write(target, "# original\n" + secret, markers=_MARKERS, project_root=tmp_path)

        assert not [entry for entry in captured_structlog if "secret.token" in str(entry)]


class TestPerformance:
    """NFR01: bounded overhead and at most one extra read."""

    def test_guard_overhead_under_budget(self, tmp_path: Path) -> None:
        target = tmp_path / "AGENTS.md"
        body = "\n".join(f"# line {i}" for i in range(2000)) + "\n"
        target.write_text(body, encoding="utf-8")
        candidate = body + f"\n{TRW_MARKER_START}\nBLOCK\n{TRW_MARKER_END}\n"

        start = time.perf_counter()
        verdict = guarded_instruction_write(target, candidate, markers=_MARKERS, project_root=tmp_path)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert verdict.written is True
        assert elapsed_ms < 50.0, f"guard added {elapsed_ms:.1f} ms on a 2000-line file"

    def test_guard_reads_the_target_at_most_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        reads: list[str] = []
        real_read = Path.read_text

        def _counting(self: Path, *args: object, **kwargs: object) -> str:
            if self == target:
                reads.append(str(self))
            return real_read(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", _counting)
        guarded_instruction_write(target, "# original\n# more\n", markers=_MARKERS, project_root=tmp_path)

        assert len(reads) == 1


class TestAdjacentPathsPreserved:
    """Behaviours this PRD must not break."""

    def test_pointer_file_is_left_unclobbered(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("@AGENTS.md\n", encoding="utf-8")

        verdict = merge_trw_section(target, _rendered_section(), 500, project_root=tmp_path)

        assert verdict.written is False
        assert target.read_text(encoding="utf-8") == "@AGENTS.md\n"


@dataclass(frozen=True)
class _WriteSite:
    """One source location that writes a user instruction surface."""

    module: str
    lineno: int
    target: str


#: Filenames of the instruction surfaces a USER co-authors. ``.trw`` artifacts
#: are TRW-owned and out of scope by the PRD's own exclusion (the PRD-CORE-203
#: sidecar `.trw/INSTRUCTIONS.md`, `.trw/context/compact_instructions.txt`), so
#: the predicate keys on the surface FILENAME rather than on the word
#: "instruction", which matched both.
_SURFACE_FILENAMES = ("agents.md", "claude.md", "instructions.md")

#: Variable names that denote an instruction surface even when the assignment
#: that built them is out of reach (a parameter, a comprehension, another module).
_SURFACE_NAME_HINTS = ("agents_md", "agents_path", "agents_file", "claude_md")


#: Persistence-class names whose ``write_text``/``write_bytes`` takes the
#: target as its FIRST POSITIONAL ARGUMENT rather than being called ON the
#: target (``FileStateWriter().write_text(target, content)``, not
#: ``target.write_text(content)``). Missing this shape let
#: ``state/claude_md/_instruction_carrier.py::heal_pointer`` write an
#: instruction surface invisibly to the scan (small-fix-4 item 4).
_PERSISTENCE_WRITER_CLASSES = ("FileStateWriter", "FileStateReader")


def _is_persistence_writer_receiver(value: ast.expr, assignments: dict[str, str] | None) -> bool:
    """Whether *value* is a ``FileStateWriter``/``FileStateReader`` constructor call or instance.

    Two forms: an inline constructor call as the receiver
    (``FileStateWriter().write_text(...)``) needs no assignment context. A
    stored instance (``writer = FileStateWriter(); ...; writer.write_text(...)``)
    resolves through *assignments*, the same name -> unparsed-value map the
    direct scan already builds; ``None`` (no assignments available, e.g. the
    per-function-local caller) degrades to recognizing only the inline form.
    """
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id in _PERSISTENCE_WRITER_CLASSES
    if isinstance(value, ast.Name) and assignments is not None:
        bound = assignments.get(value.id, "")
        return any(bound.startswith(f"{cls}(") for cls in _PERSISTENCE_WRITER_CLASSES)
    return False


def _write_target(node: ast.Call, assignments: dict[str, str] | None = None) -> ast.expr | None:
    """Return the path expression a call writes to, or ``None`` if it is not a write."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"write_text", "write_bytes"}:
        if node.args and _is_persistence_writer_receiver(func.value, assignments):
            # FileStateWriter().write_text(target, content): the write TARGET is
            # the first positional argument, not the receiver.
            return node.args[0]
        return func.value
    if isinstance(func, ast.Attribute) and func.attr == "open":
        modes = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if any("w" in m.value or "a" in m.value for m in modes):
            return func.value
        return None
    if isinstance(func, ast.Name) and func.id == "open" and node.args:
        modes = [a for a in node.args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if any("w" in m.value or "a" in m.value for m in modes):
            return node.args[0]
    return None


def _resolve(expr: ast.expr, assignments: dict[str, str]) -> str:
    """Render *expr* as source, substituting a simple local name for its assignment."""
    if isinstance(expr, ast.Name):
        return assignments.get(expr.id, expr.id)
    return ast.unparse(expr)


def _names_a_surface(target: str, expr: ast.expr) -> bool:
    """Whether *target* denotes a user-authored instruction surface."""
    lowered = target.lower()
    if any(name in lowered for name in _SURFACE_FILENAMES):
        return True
    return isinstance(expr, ast.Name) and any(hint in expr.id.lower() for hint in _SURFACE_NAME_HINTS)


def _scan_source_for_instruction_writes(source: str, *, module: str) -> list[_WriteSite]:
    """Return every site in *source* that writes a user instruction surface."""
    tree = ast.parse(source)
    assignments: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assignments[tgt.id] = ast.unparse(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assignments[node.target.id] = ast.unparse(node.value)

    sites: list[_WriteSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        expr = _write_target(node, assignments)
        if expr is None:
            continue
        target = _resolve(expr, assignments)
        if _names_a_surface(target, expr):
            sites.append(_WriteSite(module=module, lineno=node.lineno, target=target))
    return sites


@dataclass(frozen=True)
class _ParametrizedWrite:
    """A write whose target is an unresolved bare parameter of its function.

    ``_names_a_surface`` only recognizes a target named after the surface
    (e.g. ``claude_md``) or a filename literal in its own unparsed text. A
    generic writer -- ``dest.write_text(content)`` where ``dest`` is just a
    parameter -- resolves to neither, so it was silently treated as "not a
    surface" regardless of what its callers actually pass. That is how
    ``bootstrap/_file_ops.py::_write_if_missing`` escaped the FR06 scan for a
    full cycle while one of its four call sites clobbers CLAUDE.md.
    """

    def_module: str
    fn_name: str
    param_name: str
    param_index: int
    write_lineno: int


def _function_params_and_locals(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[dict[str, int], set[str], dict[str, str]]:
    """Return *fn*'s params by name -> index, names reassigned in its body, and
    a name -> unparsed-value map of those reassignments (for target resolution).
    """
    params = {a.arg: i for i, a in enumerate(fn.args.args)}
    assigned: set[str] = set()
    local_values: dict[str, str] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            assigned.update(t.id for t in n.targets if isinstance(t, ast.Name))
            for t in n.targets:
                if isinstance(t, ast.Name):
                    local_values[t.id] = ast.unparse(n.value)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
            assigned.add(n.target.id)
            local_values[n.target.id] = ast.unparse(n.value)
    return params, assigned, local_values


def _collect_parametrized_writes(root: Path) -> list[_ParametrizedWrite]:
    """Find every write whose target is a parameter with no local assignment."""
    found: list[_ParametrizedWrite] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = str(path.relative_to(root))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            params, assigned, local_values = _function_params_and_locals(fn)
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                tgt = _write_target(n, local_values)
                if isinstance(tgt, ast.Name) and tgt.id in params and tgt.id not in assigned:
                    found.append(_ParametrizedWrite(module, fn.name, tgt.id, params[tgt.id], n.lineno))
    return found


def _call_argument_for_param(call: ast.Call, param_index: int, param_name: str) -> ast.expr | None:
    """Return the expression bound to *param_name* at *call*, by keyword or position."""
    for kw in call.keywords:
        if kw.arg == param_name:
            return kw.value
    if param_index < len(call.args):
        return call.args[param_index]
    return None


def _widen_via_call_sites(root: Path, parametrized: list[_ParametrizedWrite]) -> list[_WriteSite]:
    """Resolve each bare-parameter write through its (one-level) call sites.

    For every call to a function with a parametrized write, the bound argument
    expression is classified the same way a direct target is. A call site whose
    argument denotes a surface (e.g. ``target_dir / "CLAUDE.md"``) surfaces a
    widened site at the CALL location -- that is where a reviewer or the
    ALLOWLIST must reason about it, not the generic writer's own body.
    """
    by_name: dict[str, list[_ParametrizedWrite]] = {}
    for p in parametrized:
        by_name.setdefault(p.fn_name, []).append(p)
    if not by_name:
        return []

    widened: list[_WriteSite] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        module = str(path.relative_to(root))
        assignments: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        assignments[tgt.id] = ast.unparse(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                assignments[node.target.id] = ast.unparse(node.value)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            fn_name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if fn_name is None:
                continue
            for candidate in by_name.get(fn_name, []):
                arg = _call_argument_for_param(node, candidate.param_index, candidate.param_name)
                if arg is None:
                    continue
                target = _resolve(arg, assignments)
                if _names_a_surface(target, arg):
                    widened.append(_WriteSite(module=module, lineno=node.lineno, target=target))
    return widened


def _scan_instruction_writes(root: Path) -> list[_WriteSite]:
    """Scan the whole package for writes to a user instruction surface.

    Two passes: a direct AST walk (resolves local assignments and Name hints),
    then a widening pass that follows any bare-parameter write target to its
    call sites and classifies by the argument actually passed there (FR06 gap
    closed 2026-09-03 -- see ``_ParametrizedWrite``).
    """
    sites: list[_WriteSite] = []
    for path in sorted(root.rglob("*.py")):
        sites.extend(
            _scan_source_for_instruction_writes(path.read_text(encoding="utf-8"), module=str(path.relative_to(root)))
        )
    sites.extend(_widen_via_call_sites(root, _collect_parametrized_writes(root)))
    return sites


def _referenced_names(path: Path) -> set[str]:
    """Return every identifier referenced anywhere in *path*'s AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


def _replace_without_force(project_root: Path, target: Path) -> bool:
    """Attempt the cursor-cli wholesale replacement WITHOUT force, via the guard."""
    verdict = guarded_instruction_write(
        target,
        "<!-- TRW:BEGIN -->\nTRW BODY\n<!-- TRW:END -->\n",
        markers=("<!-- TRW:BEGIN -->", "<!-- TRW:END -->"),
        project_root=project_root,
    )
    return verdict.written


def _run_sync_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, dry_run: bool = False
) -> tuple[dict[str, object], dict[Path, bytes]]:
    """Run the real ``trw_instructions_sync`` tool against a temp project."""
    from tests._tools_learning_shared import _get_tools

    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# My project rules\n\n- never delete this\n", encoding="utf-8")
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda *a, **k: tmp_path / ".trw")

    before = {claude_md: claude_md.read_bytes()}
    tools = _get_tools()
    result = tools["trw_instructions_sync"].fn(client="claude-code", dry_run=dry_run)
    return dict(result), before
