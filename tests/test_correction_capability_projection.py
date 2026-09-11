"""Correction is discoverable without unlocking in generated client guidance.

These are source-renderer and disposable instruction-generation checks, not
proof that a running host has refreshed its cached tools. The manifest seam
projects static admission; task labels do not imply runtime pack resolution.
"""

from pathlib import Path

import pytest

from trw_mcp.bootstrap._client_integration_appendix import build_client_integration_appendix
from trw_mcp.bootstrap._utils import SUPPORTED_IDES
from trw_mcp.models.surface_packs import STANDARD_TASK_PACKS


def _assert_correction_available(text: str) -> None:
    available = next(line for line in text.splitlines() if line.startswith("- **Available now"))
    discoverable = next(line for line in text.splitlines() if line.startswith("- **Discoverable via"))
    gated = next(line for line in text.splitlines() if line.startswith("- **Operator-grant only"))
    assert available.count("trw_learn_update") == 1
    assert "trw_learn_update" not in discoverable
    assert "trw_learn_update" not in gated


@pytest.mark.parametrize("client_id", SUPPORTED_IDES)
@pytest.mark.parametrize("task_type", tuple(STANDARD_TASK_PACKS))
def test_correction_available_in_native_projection(client_id: str, task_type: str) -> None:
    appendix = build_client_integration_appendix(client_id, task_type=task_type)
    assert not appendix.parity_failures
    assert f"<!-- trw:capabilities:{client_id} -->" in appendix.text
    assert f"## Resolved capabilities ({task_type})" in appendix.text
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
    _assert_correction_available(first.decode())
    generator(tmp_path, False)
    assert instructions.read_bytes() == first
    assert agents.read_bytes() == before
    assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} == {
        "AGENTS.md",
        ".codex/INSTRUCTIONS.md",
    }


def test_agents_sync_preserves_user_prose_and_projects_correction(tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.claude_md._agents_md import _sync_agents_md_if_needed

    agents = tmp_path / "AGENTS.md"
    prefix = "# Project instructions\n\nUser-owned preamble.\n"
    agents.write_text(prefix, encoding="utf-8")
    config = TRWConfig(trw_dir=str(tmp_path / ".trw"), agents_md_learning_injection=False)
    args = (True, config, tmp_path, tmp_path / ".trw", "codex")
    written, path, verdict = _sync_agents_md_if_needed(*args)
    assert written and path == str(agents) and verdict is not None
    first = agents.read_bytes()
    assert first.decode().startswith(prefix)
    _assert_correction_available(first.decode())
    _sync_agents_md_if_needed(*args)
    assert agents.read_bytes() == first
