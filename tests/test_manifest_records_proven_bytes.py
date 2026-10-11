"""The manifest records the digest of the very bytes it proved TRW's (PRD-FIX-121's laundering class).

The recorders checked ownership with one read of the installed file and then hashed the file AGAIN for the record,
so a save between the two reads became TRW's baseline and the next update replaced it; a failed first read counted
as "not edited". ``proven_digest`` reads once: the bytes judged are the bytes recorded, and an unreadable file has
no record. ``tests/AGENTS.md`` lines 1 to 3, each asserting the interleaving ran, for a key of each recorder.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests._fs_hazards import atomic_replace, edit_in_place, fail_nth_read, race_after

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_MINE = b"# Our own text\nSaved while TRW was writing its manifest.\n"
#: ``(recorder, content_hashes key)``: one key per converted read path.
_CASES = {
    "core-skill-md": ("core_artifacts", "trw-handoff/SKILL.md"),
    "core-hook": ("core_artifacts", "session-start.sh"),
    "codex-skill": ("codex_artifacts", ".agents/skills/trw-audit/SKILL.md"),
    "managed-client-skill": ("managed_client_artifacts", ".github/skills/trw-audit/SKILL.md"),
    "cursor-rule": ("managed_client_artifacts", ".cursor/rules/trw-ceremony.mdc"),
}
_each_case = pytest.mark.parametrize("case", list(_CASES.values()), ids=list(_CASES))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    from trw_mcp.bootstrap import init_project

    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    assert not init_project(root, ide="all")["errors"]
    manifest = yaml.safe_load((root / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    return root, dict(manifest["content_hashes"])


def _record(root: Path, prev: dict[str, str], recorder: str) -> dict[str, str]:
    from trw_mcp.bootstrap._manifest_recorders import MANIFEST_RECORDERS

    return next(r for r in MANIFEST_RECORDERS if r.name == recorder).record(root, prev, None)


def _installed(root: Path, key: str) -> Path:
    from trw_mcp.bootstrap._version_manifest import _manifest_key_path

    dest = root / _manifest_key_path(key)
    assert dest.is_file(), f"precondition: {key} is installed"
    return dest


@_each_case
@pytest.mark.parametrize("save", [atomic_replace, edit_in_place], ids=["atomic-replace", "in-place"])
def test_a_save_between_the_proof_and_the_record_never_becomes_the_baseline(
    project: tuple[Path, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str],
    save: Callable[[Path, bytes], None],
) -> None:
    root, prev = project
    recorder, key = case
    dest = _installed(root, key)
    shipped = dest.read_bytes()
    assert prev[key] == _sha(shipped), "precondition: the file is TRW's recorded write"
    # The recorder's first read of the file is the one it proves; the save lands right after it.
    probe = race_after(monkeypatch, target=dest, op="read_bytes", when="after", interloper=lambda: save(dest, _MINE))

    hashes = _record(root, prev, recorder)

    assert probe.fired, "the interleaving did not run"
    assert dest.read_bytes() == _MINE
    assert hashes.get(key) != _sha(_MINE), "the user's bytes were recorded as TRW's"
    assert hashes.get(key) == _sha(shipped), "what is recorded is what was proven"

    # The run after sees the user's bytes against that record: an edit, so no record at all.
    assert key not in _record(root, hashes, recorder)


@_each_case
def test_a_failed_first_read_records_nothing(
    project: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, case: tuple[str, str]
) -> None:
    """ "Could not read" is "cannot prove": it used to count as "not edited", and the second read then recorded
    whatever was there, here the user's edit."""
    root, prev = project
    recorder, key = case
    dest = _installed(root, key)
    dest.write_bytes(_MINE)
    counter = fail_nth_read(monkeypatch, dest, nth=1)

    hashes = _record(root, prev, recorder)

    assert counter.raised == 1
    assert key not in hashes
    assert dest.read_bytes() == _MINE


@_each_case
def test_there_is_no_second_read_to_fail_or_to_race(
    project: tuple[Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch, case: tuple[str, str]
) -> None:
    root, prev = project
    recorder, key = case
    dest = _installed(root, key)
    shipped = dest.read_bytes()
    counter = fail_nth_read(monkeypatch, dest, nth=2)

    hashes = _record(root, prev, recorder)

    assert (counter.calls, counter.raised) == (1, 0), "the recorder read the file more than once"
    assert hashes[key] == _sha(shipped)


@_each_case
def test_an_edited_file_is_not_recorded_and_an_unedited_one_is(
    project: tuple[Path, dict[str, str]], case: tuple[str, str]
) -> None:
    """Controls with no race, both answers (these hold before the change too)."""
    root, prev = project
    recorder, key = case
    dest = _installed(root, key)

    assert _record(root, prev, recorder)[key] == _sha(dest.read_bytes())

    dest.write_bytes(_MINE)

    assert key not in _record(root, prev, recorder)
