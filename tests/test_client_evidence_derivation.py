"""TRW's own scaffolding must never count as the user's client evidence.

``_file_evidenced_clients`` decides which clients a bare ``update-project``
adopts into the APPEND-ONLY ``target_platforms`` record. It hand-listed exactly
one exclusion — claude-code, "precisely because TRW creates ``.claude/`` itself"
— while ``.cursor/`` sat in the same list, a directory TRW creates in any
project on a machine with the ``cursor`` binary on PATH. An exclusion set of one
with no derivation is wiring-defect pattern P11: correct the day it was written,
wrong the moment the fabricating surface grew.

Measured before the fix: ``init-project --ide codex`` -> ``['codex']``; bare
update #1 -> ``['codex']`` and created ``.cursor/``; bare update #2 ->
``['codex', 'cursor-ide']``, permanently, plus the shared ``AGENTS.md`` that
PRD-CORE-240-FR04 withdrew.

Both layers are pinned: the write path no longer scaffolds a client the record
does not name, and the evidence predicate no longer trusts a path TRW writes for
somebody else.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap._template_claude_md import (
    _CLIENT_EVIDENCE_MARKERS,
    _file_evidenced_clients,
    _recorded_plus_newly_adopted,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES


def _write_record(root: Path, clients: list[str]) -> None:
    trw = root / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    (trw / "config.yaml").write_text(yaml.safe_dump({"target_platforms": clients}), encoding="utf-8")


@pytest.mark.integration
def test_trw_created_cursor_dir_is_not_evidence_of_cursor_ide(tmp_path: Path) -> None:
    """The exact shape TRW leaves behind: `.cursor/` full of TRW artifacts."""
    for sub in ("rules", "agents", "commands", "hooks", "skills"):
        (tmp_path / ".cursor" / sub).mkdir(parents=True)
    (tmp_path / ".cursor" / "mcp.json").write_text("{}", encoding="utf-8")

    assert "cursor-ide" not in _file_evidenced_clients(tmp_path)


@pytest.mark.integration
def test_trw_created_claude_dir_is_not_evidence_of_claude_code(tmp_path: Path) -> None:
    """Regression guard for the one exclusion that was already right.

    ``.claude/`` is a framework-CORE surface — written into every project
    whatever the client — so it can never distinguish clients.
    """
    for sub in ("agents", "hooks", "skills", "commands"):
        (tmp_path / ".claude" / sub).mkdir(parents=True)

    assert "claude-code" not in _file_evidenced_clients(tmp_path)


@pytest.mark.integration
def test_cursor_cli_json_is_still_evidence(tmp_path: Path) -> None:
    """Precision control: excluding `.cursor/` must not exclude everything under it.

    ``.cursor/cli.json`` is claimed by no OTHER client's surface set, so it stays
    a usable signal. A fix that dropped the whole directory tree would silently
    retire cursor-cli adoption too.
    """
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "cli.json").write_text("{}", encoding="utf-8")

    found = _file_evidenced_clients(tmp_path)
    assert "cursor-cli" in found
    assert "cursor-ide" not in found


@pytest.mark.integration
@pytest.mark.parametrize(
    ("marker", "is_dir", "client"),
    [
        (".opencode", True, "opencode"),
        ("opencode.json", False, "opencode"),
        (".codex", True, "codex"),
        (".github/agents", True, "copilot"),
        ("ANTIGRAVITY.md", False, "antigravity-cli"),
    ],
)
def test_user_side_markers_still_evidence_their_client(tmp_path: Path, marker: str, is_dir: bool, client: str) -> None:
    """Non-vacuity: the predicate must still adopt a genuinely new client."""
    path = tmp_path / marker
    if is_dir:
        path.mkdir(parents=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    assert client in _file_evidenced_clients(tmp_path)


@pytest.mark.integration
def test_bare_update_record_absorbs_a_real_adoption_not_our_own_dirs(tmp_path: Path) -> None:
    """The consumer's contract, both directions, in one project."""
    _write_record(tmp_path, ["codex"])
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".cursor" / "rules").mkdir(parents=True)  # TRW's own output
    (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")  # the user's

    assert _recorded_plus_newly_adopted(tmp_path) == ["codex", "opencode"]


@pytest.mark.unit
def test_every_supported_client_declares_an_evidence_marker() -> None:
    """P11 totality: the marker table is not allowed to be a silent subset.

    A new client added to ``SUPPORTED_IDES`` must state its candidate marker
    here, even if the derivation then rejects it. Omission is what made the
    original list wrong.
    """
    assert set(_CLIENT_EVIDENCE_MARKERS) == set(SUPPORTED_IDES)


@pytest.mark.unit
def test_scaffolded_markers_are_rejected_by_derivation_not_by_a_literal() -> None:
    """The exclusion is computed from the client-surface registry.

    Asserting the mechanism, not just its current output: feed the predicate a
    path the registry claims for another client and it must reject it, and feed
    it one nobody claims and it must accept it.
    """
    from trw_mcp.bootstrap._template_claude_md import _trw_scaffolds_marker

    assert _trw_scaffolds_marker("cursor-ide", ".cursor") is True  # cursor-cli writes .cursor/cli.json
    assert _trw_scaffolds_marker("claude-code", ".claude") is True  # core surface, every project
    assert _trw_scaffolds_marker("cursor-cli", ".cursor/cli.json") is False
    assert _trw_scaffolds_marker("codex", ".codex") is False


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
