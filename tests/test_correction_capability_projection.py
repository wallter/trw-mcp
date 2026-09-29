"""Correction is discoverable without unlocking in generated client guidance.

These are source-renderer and disposable instruction-generation checks, not
proof that a running host has refreshed its cached tools. The manifest seam
projects static admission; PRD-CORE-300 S11b deleted per-task pack resolution
entirely, so ``task_type`` is now a vestigial default-value parameter on the
appendix builder — the capability projection it renders no longer varies by
task, so this file no longer parametrizes over the deleted
``STANDARD_TASK_PACKS`` vocabulary.
"""

from pathlib import Path

import pytest

from trw_mcp.bootstrap._client_integration_appendix import build_client_integration_appendix
from trw_mcp.bootstrap._utils import SUPPORTED_IDES


def _assert_correction_available(text: str) -> None:
    """Correction must be enumerated as available via ``trw_learn``, never a separate tool.

    ``dde1c6fb6`` (PRD-FIX-140-FR08) stopped enumerating the discoverable and
    operator-gated classes and collapsed them into a single counts-only bullet.
    PRD-CORE-291 then merged ``trw_learn_update`` into ``trw_learn``'s
    update mode. PRD-CORE-300 S11b then deleted the discoverable tier
    outright, flattening the listing to available/gated. The assertion is now:
    the AVAILABLE bullet names ``trw_learn`` (the correction path, always
    on — it is in the kernel), and ``trw_learn_update`` is named nowhere in
    either bullet — it no longer exists as a separate tool.
    """
    available = next(line for line in text.splitlines() if line.startswith("- **Available in every session**"))
    gated = next(line for line in text.splitlines() if line.startswith("- **Behind a config flag**"))
    assert "trw_learn" in available
    assert "trw_learn_update" not in available
    assert "trw_learn_update" not in gated


def _assert_correction_named(text: str) -> None:
    """A generated carrier names the correction path, never the removed tool.

    PRD-CORE-301-FR13 moved the capability listing out of generated carriers
    (it is served by ``trw_status(detail="surface")``), so a carrier proves the
    correction path by its memory rule: ``trw_learn`` with ``learning_id``.
    """
    assert "`trw_learn()`" in text
    assert "learning_id=..." in text
    assert "trw_learn_update" not in text


@pytest.mark.parametrize("client_id", SUPPORTED_IDES)
def test_correction_available_in_native_projection(client_id: str) -> None:
    appendix = build_client_integration_appendix(client_id)
    assert not appendix.parity_failures
    assert f"<!-- trw:capabilities:{client_id} -->" in appendix.text
    assert "## Resolved capabilities" in appendix.text
    _assert_correction_available(appendix.text)


def test_codex_sync_generator_emits_correction_without_other_project_writes(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._instruction_clients import _INSTRUCTION_SYNC_GENERATORS

    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Project guidance\nKeep this unchanged.\n", encoding="utf-8")
    before = agents.read_bytes()
    generator = _INSTRUCTION_SYNC_GENERATORS["codex"]
    generator(tmp_path, False)
    instructions = tmp_path / ".codex" / "INSTRUCTIONS.md"
    first = instructions.read_bytes()
    _assert_correction_named(first.decode())
    generator(tmp_path, False)
    assert instructions.read_bytes() == first
    assert agents.read_bytes() == before
    assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} == {
        "AGENTS.md",
        ".codex/INSTRUCTIONS.md",
    }


def test_agents_sync_preserves_user_prose_and_projects_correction(tmp_path: Path) -> None:
    """AGENTS.md keeps the user's prose and links the instructions file, which names the correction path."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.claude_md._agents_md import _sync_agents_md_if_needed
    from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH, LINK_BODY

    agents = tmp_path / "AGENTS.md"
    instructions = tmp_path / INSTRUCTIONS_RELPATH
    prefix = "# Project instructions\n\nUser-owned preamble.\n"
    agents.write_text(prefix, encoding="utf-8")
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
    args = (True, config, tmp_path, "codex")
    written, path, verdicts = _sync_agents_md_if_needed(*args)
    assert written and path == str(agents) and verdicts
    first = agents.read_bytes()
    first_instructions = instructions.read_bytes()
    assert first.decode().startswith(prefix)
    assert LINK_BODY in first.decode()
    _assert_correction_named(first_instructions.decode())
    _sync_agents_md_if_needed(*args)
    assert agents.read_bytes() == first
    assert instructions.read_bytes() == first_instructions
