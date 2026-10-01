"""TRW's own scaffolding must never count as the user's client evidence.

A bare ``update-project`` used to adopt a client into the APPEND-ONLY ``target_platforms`` record from a
marker path alone. It now adopts only on file-by-file proof (``_client_adoption``), so what is pinned
here is the outcome: ``init-project --ide codex`` plus bare updates leaves the record ``['codex']`` and
never scaffolds ``.cursor/``, and the install-time marker table stays total over ``SUPPORTED_IDES``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap._template_claude_md import (
    _CLIENT_EVIDENCE_MARKERS,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


@pytest.mark.unit
def test_every_supported_client_declares_an_evidence_marker() -> None:
    """P11 totality: the marker table is not allowed to be a silent subset.

    A new client added to ``SUPPORTED_IDES`` must state its candidate marker
    here, even if the derivation then rejects it. Omission is what made the
    original list wrong.
    """
    assert set(_CLIENT_EVIDENCE_MARKERS) == set(SUPPORTED_IDES)


@pytest.mark.slow
def test_bare_update_on_a_codex_project_never_adopts_cursor_ide(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """End-to-end reproduction: init --ide codex, then two bare updates.

    ``detect_ide`` is stubbed to the shape a developer machine with Cursor
    installed produces (``shutil.which("cursor")`` -> cursor-ide) so the test
    measures TRW's behaviour rather than the runner's PATH.
    """
    from trw_mcp.bootstrap import _utils
    from trw_mcp.bootstrap._init_project import init_project
    from trw_mcp.bootstrap._update_project import update_project

    def fake_detect_ide(target_dir: Path) -> list[str]:
        detected = ["cursor-ide"]  # machine-global: the IDE launcher is on PATH
        if (target_dir / ".claude").is_dir():
            detected.insert(0, "claude-code")
        if (target_dir / ".codex").is_dir():
            detected.append("codex")
        return detected

    monkeypatch.setattr(_utils, "detect_ide", fake_detect_ide)
    (tmp_path / ".git").mkdir()

    init_project(tmp_path, ide="codex")
    update_project(tmp_path)
    update_project(tmp_path)

    recorded = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))
    assert recorded["target_platforms"] == ["codex"]
    assert not (tmp_path / ".cursor").exists()
    # Non-vacuity: the update actually ran and refreshed the recorded client.
    assert (tmp_path / ".codex").is_dir()


@pytest.mark.slow
def test_bare_update_never_writes_an_unrecorded_clients_hook_env_and_names_how_to_add_it(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """UPDATE-PROJECT-RECORDED-CLIENTS-ONLY (codex UF-BOOT-07-r1 KI3): update-project touched a detected-but-
    unrecorded client: `ide_targets` came from detection and drove the hook-env rewrite, so a codex-only project
    on a machine with Cursor got Cursor's hook environment. Only recorded clients (or an explicit --ide) are
    written; a detected extra gets one line saying how to add it."""
    from trw_mcp.bootstrap import _utils
    from trw_mcp.bootstrap._init_project import init_project
    from trw_mcp.bootstrap._update_project import update_project

    monkeypatch.setattr(
        _utils,
        "detect_ide",
        lambda target_dir: ["cursor-ide", *(["codex"] if (target_dir / ".codex").is_dir() else [])],
    )
    (tmp_path / ".git").mkdir()
    init_project(tmp_path, ide="codex")
    env_dir = tmp_path / ".trw" / "runtime" / "hook-env.d"
    before = sorted(p.name for p in env_dir.glob("*.sh")) if env_dir.is_dir() else []

    result = update_project(tmp_path)

    after = sorted(p.name for p in env_dir.glob("*.sh")) if env_dir.is_dir() else []
    # Codex r1 KI2: a rolled-back update would also leave the files unchanged; prove this one ran and wrote.
    assert not result["errors"], result["errors"]
    assert "codex.sh" in after, "the recorded client's hook env is still written"
    assert after == before, f"an unrecorded client's hook env was written: {sorted(set(after) - set(before))}"
    notes = [w for w in result.get("warnings", []) if "cursor-ide" in w]
    assert notes and "--ide cursor-ide" in notes[0], result.get("warnings")
