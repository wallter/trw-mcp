"""PRD-CORE-250 FR01-FR04 — four bundled hooks that no template registered are gone.

Measured at HEAD on 2026-09-03: 1,053 lines of shell shipped to every user,
hashed into ``data/bundle-hashes.json``, listed in ``.trw/managed-artifacts.yaml``
and — for three of the four — published in the ``<!-- inv:hooks -->`` count,
while matching nothing in ``data/settings.json``, nothing in
``data/plugin/hooks/hooks.json`` and nothing under ``bootstrap/``. None of them
could execute.

Deleting a file is trivially "done"; what these tests pin is that nothing was
enforcing through them and nothing stopped enforcing with them. Each absence
assertion is therefore paired with the live gate that covers the same ground:

* FR01 ``completion-gate.sh`` claimed the build-check requirement that
  ``pre-tool-deliver-gate.sh`` actually blocks on;
* FR03 ``phase-cycle-stop.sh`` claimed a Stop gate that ``stop-ceremony.sh``
  actually holds.

Without the paired assertion, a change that deleted the coverage too would pass
this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp"
_HOOK_DIR = _SRC / "data" / "hooks"
_BUNDLE_HASHES = _SRC / "data" / "bundle-hashes.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Every surface that ships or records a bundled hook. A deleted hook must be
#: absent from ALL of them: a stale bundle-hashes entry makes an install verify
#: content that no longer exists, and a stale manifest entry is what the update
#: sweep reads.
_DELETED = ("completion-gate.sh", "helper-idle.sh", "phase-cycle-stop.sh", "lib-ide-adapter.sh")


def _bundle_hash_keys() -> set[str]:
    document = json.loads(_BUNDLE_HASHES.read_text(encoding="utf-8"))
    files = document.get("entries", document)
    return {Path(key).name for key in files}


def test_completion_gate_is_gone() -> None:
    assert list(_HOOK_DIR.glob("completion-gate.sh")) == []
    assert "completion-gate.sh" not in _bundle_hash_keys()


def test_the_deliver_gate_still_blocks_an_unverified_deliver() -> None:
    """FR01's paired coverage assertion — the condition is still enforced.

    ``completion-gate.sh`` claimed to require a passing build check before a task
    could complete. That requirement is live in ``pre-tool-deliver-gate.sh``,
    which IS registered in both shipped templates. This asserts the enforcing
    branch is still present and still reachable, so "we deleted the dead copy"
    cannot silently become "we deleted the requirement".
    """
    gate = (_HOOK_DIR / "pre-tool-deliver-gate.sh").read_text(encoding="utf-8")
    assert "exit 2" in gate, "the deliver gate no longer blocks anything"
    assert "allow_unverified" in gate, "the documented recourse path is gone"

    for template in (
        _SRC / "data" / "settings.json",
        _SRC / "data" / "plugin" / "hooks" / "hooks.json",
    ):
        assert "pre-tool-deliver-gate.sh" in template.read_text(encoding="utf-8"), (
            f"{template.name} no longer registers the gate that carries FR01's coverage"
        )


def test_helper_idle_is_gone() -> None:
    assert list(_HOOK_DIR.glob("helper-idle.sh")) == []
    assert "helper-idle.sh" not in _bundle_hash_keys()


def test_phase_cycle_stop_is_gone_and_stop_ceremony_unchanged() -> None:
    """FR03: the deleted Stop gate, and the Stop gate that actually ships.

    ``phase-cycle-stop.sh`` blocked Stop with exit 2 on phase-exit criteria and
    was registered nowhere. ``stop-ceremony.sh`` is the registered Stop hook; its
    behaviour is asserted in full by ``test_stop_ceremony_unpinned.py`` and
    ``test_stop_ceremony_unpinned_deliver.sh``. Here we only pin that FR03
    removed a gate and added none: the Stop surface still has exactly one hook.
    """
    assert list(_HOOK_DIR.glob("phase-cycle-stop.sh")) == []
    assert list((_REPO_ROOT / "trw-mcp" / "tests" / "hooks").glob("test_plan_gate_prd_score.sh")) == []
    assert "phase-cycle-stop.sh" not in _bundle_hash_keys()

    settings = json.loads((_SRC / "data" / "settings.json").read_text(encoding="utf-8"))
    stop_commands = [hook["command"] for entry in settings["hooks"]["Stop"] for hook in entry["hooks"]]
    assert len(stop_commands) == 1, f"FR03 must not add a Stop gate: {stop_commands}"
    assert "stop-ceremony.sh" in stop_commands[0]


def test_lib_ide_adapter_is_gone() -> None:
    """FR04: the file, and every reference from a bundled hook.

    It could never have been sourced — it declared ``set -euo pipefail`` at top
    level while every bundled hook runs under ``sh`` — so a lingering reference
    would be a broken source, not a working one.
    """
    assert list(_HOOK_DIR.glob("lib-ide-adapter.sh")) == []
    assert "lib-ide-adapter.sh" not in _bundle_hash_keys()

    referencing = [
        path.name for path in sorted(_HOOK_DIR.rglob("*.sh")) if "lib-ide-adapter" in path.read_text(encoding="utf-8")
    ]
    assert referencing == [], f"bundled hook(s) still reference the deleted library: {referencing}"


@pytest.mark.parametrize("name", _DELETED)
def test_no_deleted_hook_survives_in_any_shipping_surface(name: str) -> None:
    """The whole set, across every surface that ships or records a hook."""
    assert not (_HOOK_DIR / name).exists()
    assert name not in _bundle_hash_keys()
    for template in (
        _SRC / "data" / "settings.json",
        _SRC / "data" / "plugin" / "hooks" / "hooks.json",
    ):
        assert name not in template.read_text(encoding="utf-8")


def test_the_private_phase_inference_copy_went_with_its_host() -> None:
    """PRD-FIX-124-FR11's obligation, retargeted at the directory.

    That FR asserted ``grep_absent: "_pcs_infer_phase"`` against
    ``phase-cycle-stop.sh``. Deleting the host file satisfies the obligation but
    makes the assertion's target vanish, which would let the symbol return in a
    different hook unnoticed. Scanning the directory keeps it falsifiable.
    """
    carriers = [
        path.name for path in sorted(_HOOK_DIR.rglob("*.sh")) if "_pcs_infer_phase" in path.read_text(encoding="utf-8")
    ]
    assert carriers == [], f"a private recency-bound phase copy is back in: {carriers}"
