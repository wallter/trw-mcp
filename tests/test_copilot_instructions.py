"""Copilot instruction generation and merge tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_INSTRUCTIONS_PATH,
    _COPILOT_TRW_END_MARKER,
    _COPILOT_TRW_START_MARKER,
    _copilot_instructions_content,
    generate_copilot_instructions,
)
from trw_mcp.bootstrap._file_ops import smart_merge_marker_section

from ._copilot_test_support import fake_git_repo  # noqa: F401


def resolve_copilot_instructions(root: Path) -> str:
    """Return copilot's instruction text with its ``@``-import resolved.

    The TRW block reaches `.github/copilot-instructions.md` through a carrier
    (PRD-CORE-240-FR03): either INLINE between the copilot markers, or
    externalized to a `.trw/` sidecar with a single `@<relpath>` import left in
    its place. Assertions about protocol content must hold under both, so they
    run against the resolved text.

    Stronger than the raw read it replaces: a dangling import contributes
    nothing here, so the protocol assertion fails — which is correct, and is a
    failure a raw read could not distinguish from success.

    Resolution is repo-root-relative, matching what the shipped Copilot CLI
    bundle does for the repository case.
    """
    text = (root / _COPILOT_INSTRUCTIONS_PATH).read_text(encoding="utf-8")
    parts = [text]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("@") and len(stripped.split()) == 1:
            target = root / stripped[1:]
            if target.is_file():
                parts.append(target.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _merge(existing: str, trw_content: str) -> str:
    """Exercise the live production merge path with the Copilot markers.

    Replaces the deleted ``_copilot._smart_merge_instructions`` deprecated
    wrapper: tests now drive ``smart_merge_marker_section`` (the sole merge
    primitive called by ``write_instruction_file_with_merge``) directly.
    """
    return smart_merge_marker_section(
        existing,
        trw_content,
        start_marker=_COPILOT_TRW_START_MARKER,
        end_marker=_COPILOT_TRW_END_MARKER,
    )


@pytest.mark.unit
class TestCopilotInstructions:
    """Test generate_copilot_instructions and smart-merge logic."""

    def test_instructions_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).is_file()
        assert _COPILOT_INSTRUCTIONS_PATH in result["created"]

    def test_instructions_contains_trw_markers(self, fake_git_repo: Path) -> None:
        generate_copilot_instructions(fake_git_repo)
        content = (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).read_text()
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_contains_ceremony_protocol(self, fake_git_repo: Path) -> None:
        """The protocol moved to the file Copilot loads itself; the gate did not.

        `.github/copilot-instructions.md` is user-owned and admits no include
        syntax, so the goal there is minimal injection, not zero. The full
        protocol now renders into `.github/instructions/trw-ceremony.instructions.md`
        with `applyTo: "**"` — a TRW-owned file. What stays inline is the deliver
        gate, because GitHub documents copilot-instructions.md as always-on
        while `.instructions.md` files apply by pattern match.
        """
        from trw_mcp.bootstrap._copilot_artifacts import generate_copilot_path_instructions
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        generate_copilot_instructions(fake_git_repo)
        generate_copilot_path_instructions(fake_git_repo)

        carrier = resolve_copilot_instructions(fake_git_repo)
        assert "TRW Framework Integration" in carrier
        assert DELIVER_GATE_PHRASE in carrier, "the gate must stay where inclusion is unconditional"

        rule = (fake_git_repo / ".github" / "instructions" / "trw-ceremony.instructions.md").read_text(encoding="utf-8")
        assert 'applyTo: "**"' in rule
        for tool in ("trw_session_start", "trw_learn", "trw_checkpoint", "trw_deliver"):
            assert tool in rule, tool

    def test_instructions_smart_merge_preserves_user_content(self, fake_git_repo: Path) -> None:
        """Existing file with user content + TRW markers → user content preserved."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)

        user_before = "# My Custom Instructions\n\nDo NOT delete this.\n\n"
        user_after = "\n\n## My Other Section\n\nKeep this too.\n"
        original_trw = f"{_COPILOT_TRW_START_MARKER}\nold content here\n{_COPILOT_TRW_END_MARKER}"
        instructions_path.write_text(user_before + original_trw + user_after)

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        resolved = resolve_copilot_instructions(fake_git_repo)
        assert "My Custom Instructions" in content
        assert "Do NOT delete this." in content
        assert "My Other Section" in content
        assert "Keep this too." in content
        # Protocol reachable via the carrier; user content stays in the file itself.
        assert "TRW Framework Integration" in resolved
        assert "old content here" not in content

    def test_instructions_fresh_file_when_no_markers(self, fake_git_repo: Path) -> None:
        """Existing file without markers gets TRW section appended."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# User instructions only\n\nNo markers here.\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "User instructions only" in content
        assert "No markers here." in content
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True completely replaces the file with TRW content."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# I will be overwritten\nUser content here.\n")

        result = generate_copilot_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "I will be overwritten" not in content
        assert _COPILOT_TRW_START_MARKER in content
        assert "TRW Framework Integration" in resolve_copilot_instructions(fake_git_repo)

    def test_instructions_updated_when_existing(self, fake_git_repo: Path) -> None:
        """Re-running on existing file marks it as updated, not created."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# Existing\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert _COPILOT_INSTRUCTIONS_PATH in result["updated"]
        assert _COPILOT_INSTRUCTIONS_PATH not in result["created"]

    def test_instructions_creates_github_dir(self, fake_git_repo: Path) -> None:
        """The .github directory is created if it doesn't exist."""
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / ".github").is_dir()


@pytest.mark.unit
class TestCopilotForceExternalizeAtomicity:
    """PRD-CORE-247 diag-canon finding: ``force`` must never leave the
    user-owned instruction file truncated on disk, even transiently.

    Copilot's shipped profile declares ``instruction_import_syntax="none"``
    (PRD-CORE-240-FR03), so ``_externalize_copilot_block`` never reaches its
    write path in production today. It is still shipped code exercised by this
    generic carrier-application logic, and the bug it carried — truncating
    ``target_path`` to ``""`` before computing the carrier, then restoring on
    failure — has a window where a crash between the truncate and the restore
    loses the file for good. These tests patch the profile to be import-capable
    so the write path executes, the way it would if ``instruction_import_syntax``
    were ever re-enabled for this client (as its own docstring notes it once was).
    """

    @pytest.fixture()
    def _import_capable_copilot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.models.config import _profiles as _profiles_module

        real_resolve = _profiles_module.resolve_client_profile

        def _patched(client_id: str, model_tier: object = None) -> object:
            profile = real_resolve(client_id, model_tier)  # type: ignore[arg-type]
            if client_id == "copilot":
                profile = profile.model_copy(update={"instruction_import_syntax": "at_path_repo_relative"})
            return profile

        monkeypatch.setattr(_profiles_module, "resolve_client_profile", _patched)
        monkeypatch.setattr("trw_mcp.bootstrap._copilot.resolve_client_profile", _patched, raising=False)

    def test_force_never_writes_empty_content_to_the_real_target_file(
        self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch, _import_capable_copilot: None
    ) -> None:
        """No call ever truncates ``target_path`` to empty, even transiently."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
        original = "# User-owned instructions\n\nDo not lose this line.\n"
        instructions_path.write_text(original, encoding="utf-8")

        empty_writes_to_target: list[str] = []
        real_write_text = Path.write_text

        def _spy_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
            if self == instructions_path and data == "":
                empty_writes_to_target.append(data)
            return real_write_text(self, data, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "write_text", _spy_write_text)

        result = generate_copilot_instructions(fake_git_repo, force=True)

        assert not empty_writes_to_target, "target_path was truncated to empty mid-write"
        assert not result["errors"]
        assert original not in instructions_path.read_text(encoding="utf-8")

    def test_swap_failure_leaves_the_original_file_untouched(
        self, fake_git_repo: Path, monkeypatch: pytest.MonkeyPatch, _import_capable_copilot: None
    ) -> None:
        """A failure at the atomic swap point never corrupts ``target_path``.

        Simulates the crash the truncate-then-restore approach could not
        recover from: something goes wrong exactly where the new content would
        land. Only the staging-to-target swap is made to fail (not every
        ``Path.replace`` call in the process), so the assertion isolates
        ``_externalize_copilot_block``'s own atomicity rather than the
        caller's separate INLINE fallback.
        """
        from trw_mcp.bootstrap._copilot import _copilot_instructions_content, _externalize_copilot_block
        from trw_mcp.bootstrap._file_ops import _new_result

        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
        original = "# User-owned instructions\n\nDo not lose this line.\n"
        instructions_path.write_text(original, encoding="utf-8")

        real_replace = Path.replace

        def _boom_replace(self: Path, target: Path) -> Path:
            if self.name.endswith(".trw-force-staging"):
                raise OSError("simulated crash during the atomic swap")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", _boom_replace)

        result = _new_result()
        handled = _externalize_copilot_block(
            instructions_path,
            fake_git_repo,
            _copilot_instructions_content(),
            result,
            force=True,
        )

        assert handled is False
        assert instructions_path.read_text(encoding="utf-8") == original, (
            "a failed atomic swap must leave the original file exactly as it was"
        )


@pytest.mark.unit
class TestSmartMergeInstructions:
    """Unit tests for the shared ``smart_merge_marker_section`` production path.

    These formerly targeted the deleted ``_copilot._smart_merge_instructions``
    deprecated wrapper; they now drive the live merge primitive directly via
    the ``_merge`` helper so equivalence is verified against production code.
    """

    def test_merge_replaces_trw_section(self) -> None:
        existing = f"before\n{_COPILOT_TRW_START_MARKER}\nold\n{_COPILOT_TRW_END_MARKER}\nafter"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert "before" in merged
        assert "after" in merged

    def test_merge_appends_when_no_markers(self) -> None:
        existing = "user content only"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew section\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "user content only" in merged
        assert "new section" in merged

    def test_merge_empty_existing(self) -> None:
        new_content = f"{_COPILOT_TRW_START_MARKER}\nstuff\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge("", new_content)
        assert "stuff" in merged

    def test_merge_missing_markers_appends_preserving_user_prose(self) -> None:
        """No markers anywhere in existing -> append; every user byte preserved.

        Line-anchored-vs-substring regression guard: an inline prose mention of
        the marker must not be mistaken for a real section boundary. Here the
        markers are genuinely absent so the whole document survives and the TRW
        block lands at the tail.
        """
        existing = "# User doc\n\nProse referencing nothing.\n"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "# User doc" in merged
        assert "Prose referencing nothing." in merged
        assert merged.endswith(new_content + "\n")

    def test_merge_duplicated_markers_replaces_first_pair_only(self) -> None:
        """Two full marker pairs -> only the FIRST pair's interior is replaced.

        Pins substring-find first-match semantics: the trailing duplicate block
        and all surrounding user text are preserved verbatim.
        """
        block1 = f"{_COPILOT_TRW_START_MARKER}\nblock one\n{_COPILOT_TRW_END_MARKER}"
        block2 = f"{_COPILOT_TRW_START_MARKER}\nblock two\n{_COPILOT_TRW_END_MARKER}"
        existing = f"pre\n{block1}\nmid\n{block2}\npost"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nfresh\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "block one" not in merged
        assert "fresh" in merged
        assert "block two" in merged
        assert "pre" in merged
        assert "mid" in merged
        assert "post" in merged

    def test_merge_crlf_file_preserves_line_endings_outside_section(self) -> None:
        """Markers in a CRLF (\\r\\n) file merge cleanly; CRLF bytes outside the
        section are untouched (the 705-line truncation footgun class)."""
        user_before = "# User\r\nline\r\n"
        user_after = "\r\ntrailer\r\n"
        original = f"{_COPILOT_TRW_START_MARKER}\r\nold\r\n{_COPILOT_TRW_END_MARKER}"
        existing = user_before + original + user_after
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert user_before in merged
        assert user_after in merged

    def test_merge_empty_existing_section_is_replaced(self) -> None:
        """An existing empty section (adjacent markers) is replaced, not appended."""
        existing = f"before\n{_COPILOT_TRW_START_MARKER}{_COPILOT_TRW_END_MARKER}\nafter"
        new_content = f"{_COPILOT_TRW_START_MARKER}\ncontent\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert "content" in merged
        assert "before" in merged
        assert "after" in merged
        # Replacement (not append): only one start/end marker pair remains.
        assert merged.count(_COPILOT_TRW_START_MARKER) == 1
        assert merged.count(_COPILOT_TRW_END_MARKER) == 1

    def test_copilot_instructions_content_has_markers(self) -> None:
        content = _copilot_instructions_content()
        assert content.startswith(_COPILOT_TRW_START_MARKER)
        assert _COPILOT_TRW_END_MARKER in content

    def test_merge_end_before_start_appends(self) -> None:
        """End marker before start marker is treated as corrupted — append instead."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nmiddle\n{_COPILOT_TRW_START_MARKER}"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert merged.endswith(new_content + "\n")
        assert "user" in merged

    def test_merge_single_start_marker_appends(self) -> None:
        """Only start marker present (no end) — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_START_MARKER}\npartial"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_single_end_marker_appends(self) -> None:
        """Only end marker present — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nstuff"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _merge(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_idempotent(self, fake_git_repo: Path) -> None:
        """Running generate_copilot_instructions twice marks second as preserved."""
        result1 = generate_copilot_instructions(fake_git_repo)
        assert result1.get("created") or result1.get("updated")
        result2 = generate_copilot_instructions(fake_git_repo)
        assert result2.get("preserved")
