"""PRD-CORE-333 FR03: nothing serves learning content from the entries mirror, bypassing the backend.

The backend read layer filters quarantined identities (FR02). The ``.trw/learnings``
entries directory is a YAML MIRROR of the store that no backend filter can reach, so a
reader of it serves whatever the mirror holds -- a learning quarantined after its
sidecar was written, or a file dropped there by hand.

* No bundled hook reads the mirror; ``user-prompt-submit.sh`` reads through the store
  (``trw_mcp.state._auto_recall_hook``), end to end against a real daemon below.
* Every trw-mcp function that reads the mirror is listed here with the reason its reads
  serve nothing. A new reader fails the census until it is reviewed and listed.
  ``KNOWN_SERVING_BYPASSES`` names the mirror readers that DO send content somewhere,
  pinned so that list can only shrink.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from tests._auto_recall_hook_harness import _HOOK_PATHS, _copy_hook_to_temp
from tests._layout import PACKAGE_ROOT, requires_monorepo

_HOOKS_DIR = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "hooks"
_SRC = PACKAGE_ROOT / "src"

#: Calls that read the mirror by name.
_MIRROR_PRIMITIVES = frozenset(
    {"iter_yaml_entry_files", "_iter_entry_files", "find_entry_by_id", "resolve_entry_file", "resolve_entry_path"}
)
#: Calls that read a directory or file; a reader when the function also names an entries dir.
_READ_CALLS = frozenset({"glob", "rglob", "iterdir", "read_yaml", "read_text", "open", "safe_load", "load"})

#: function -> why its mirror reads serve no learning content to an agent, user or remote.
MAINTENANCE_READERS: dict[str, str] = {
    "trw_mcp/audit.py::_iter_entries": "audit tallies of entry counts and fields",
    "trw_mcp/bootstrap/_init_project.py::_harden_trw_permissions": "chmod walk; reads no content",
    "trw_mcp/scoring/_io_boundary.py::_default_lookup_entry": "outcome scoring write-back path",
    "trw_mcp/scoring/_yaml_id_index.py::_build_yaml_path_index": "id -> sidecar path index",
    "trw_mcp/state/_entry_paths.py::resolve_entry_file": "sidecar path resolution for dedup merge",
    "trw_mcp/state/_helpers.py::iter_yaml_entry_files": "the iterator primitive itself",
    "trw_mcp/state/_memory_lookups.py::find_yaml_path_for_entry": "sidecar path lookup",
    "trw_mcp/state/_tier_sweep.py::_sweep_warm_to_cold": "tier archival bookkeeping",
    "trw_mcp/state/analytics/core.py::_iter_entry_files": "the iterator primitive itself",
    "trw_mcp/state/analytics/core.py::find_entry_by_id": "sidecar lookup for write-side updates",
    "trw_mcp/state/analytics/dedup.py::find_duplicate_learnings": "dedup similarity at write time",
    "trw_mcp/state/analytics/dedup.py::auto_prune_excess_entries": "prune bookkeeping",
    "trw_mcp/state/analytics/dedup.py::compute_reflection_quality": "aggregate quality metrics",
    "trw_mcp/state/analytics/entries.py::has_existing_mechanical_learning": "dedup check at write time",
    "trw_mcp/state/analytics/entries.py::resync_learning_index": "rebuilds the mirror's own index file",
    "trw_mcp/state/analytics/entries.py::apply_status_update": "sidecar status write-back",
    "trw_mcp/state/claude_md/_promotion.py::collect_patterns": "reads the PATTERNS dir, not learning entries",
    "trw_mcp/state/tiers.py::_flush_last_accessed": "access-time bookkeeping",
    "trw_mcp/state/tiers.py::assign_impact_tiers": "impact-tier bookkeeping",
    "trw_mcp/tools/_learn_side_effects.py::_handle_consolidation": "sidecar write-back on consolidation",
    "trw_mcp/tools/_learning_helpers.py::_resolve_merge_survivor": "dedup merge survivor lookup",
    "trw_mcp/tools/_learning_module_helpers.py::_sync_learning_yaml_backup": "the mirror's writer",
    "trw_mcp/tools/orchestration.py::register_orchestration_tools": "mkdir of the entries dir; reads none",
    "trw_mcp/telemetry/publisher.py::_load_hashes": "publish dedup hashes",
}
#: Mirror readers whose content DOES leave the process -- unfiltered. FR03 follow-up:
#: the platform publisher egresses summary/detail from the mirror. Pinned so it cannot grow.
KNOWN_SERVING_BYPASSES: dict[str, str] = {
    "trw_mcp/telemetry/publisher.py::publish_learnings": "publishes mirror summary/detail to the platform",
}


def _names_entries_dir(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "entries_dir" in sub.id:
            return True
        if isinstance(sub, ast.Attribute) and "entries_dir" in sub.attr:
            return True
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "learnings/entries" in sub.value:
            return True
    return False


def mirror_readers(src: Path) -> set[str]:
    """Every function under *src* that reads the entries mirror, as ``path::function``."""
    found: set[str] = set()
    for path in sorted(src.rglob("*.py")):
        for func in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {
                getattr(call.func, "id", getattr(call.func, "attr", ""))
                for call in ast.walk(func)
                if isinstance(call, ast.Call)
            }
            if calls & _MIRROR_PRIMITIVES or (calls & _READ_CALLS and _names_entries_dir(func)):
                found.add(f"{path.relative_to(src).as_posix()}::{func.name}")
    return found


def _hooks_naming_the_entries_mirror(paths: list[Path]) -> list[str]:
    return [
        str(path)
        for path in paths
        if "learnings/entries" in (text := path.read_text(encoding="utf-8")) or "_entries_dir" in text
    ]


def _assert_auto_recall_reads_through_the_store(hooks: list[Path]) -> None:
    for hook in hooks:
        text = hook.read_text(encoding="utf-8")
        assert "-m trw_mcp.state._auto_recall_hook" in text
        assert "filter_quarantined" in text


def test_bundled_hooks_no_longer_read_entries_directly() -> None:
    """No bundled hook names the entries mirror; auto-recall reads through the store."""
    bundled = [path for path in _HOOK_PATHS if path.is_relative_to(PACKAGE_ROOT / "src")]
    hooks = sorted(_HOOKS_DIR.rglob("*.sh")) + bundled
    assert len(hooks) > 10, "the census is not looking at the hook tree"
    assert _hooks_naming_the_entries_mirror(hooks) == []
    _assert_auto_recall_reads_through_the_store(bundled)


@requires_monorepo
def test_dev_mirror_hooks_no_longer_read_entries_directly() -> None:
    """The repo's own `.claude/hooks` copies (absent from a canary export) hold to the same rule."""
    mirrors = [path for path in _HOOK_PATHS if not path.is_relative_to(PACKAGE_ROOT / "src")]
    assert mirrors, "no dev-mirror hook is registered in the harness"
    assert _hooks_naming_the_entries_mirror(mirrors) == []
    _assert_auto_recall_reads_through_the_store(mirrors)


def test_every_mirror_reader_is_reviewed() -> None:
    """A new function reading the mirror fails until it is classified above."""
    found = mirror_readers(_SRC)
    assert found, "the census found no readers: it is not looking at the source tree"
    listed = MAINTENANCE_READERS.keys() | KNOWN_SERVING_BYPASSES.keys()
    assert sorted(found - listed) == [], "unreviewed mirror reader(s)"
    assert sorted(listed - found) == [], "stale census entries: remove them"


def test_census_catches_a_planted_reader(tmp_path: Path) -> None:
    """Guard the guard: a planted function globbing the entries dir is found."""
    planted = tmp_path / "trw_mcp" / "planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "def serve(entries_dir):\n    return [p.read_text() for p in entries_dir.glob('*.yaml')]\n", encoding="utf-8"
    )
    assert mirror_readers(tmp_path) == {"trw_mcp/planted.py::serve"}


def test_hook_recall_excludes_a_ledgered_identity(daemon_checkout: object, tmp_path: Path) -> None:
    """End to end: the real hook, the real filtered read, a real daemon and a real ledger row.

    Both learnings match the prompt and both sit in the entries mirror too (as
    ``trw_learn`` leaves them). The quarantined one never reaches the prompt.
    """
    from trw_memory.daemon import DaemonPaths
    from trw_memory.models.config import MemoryConfig
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    checkout = daemon_checkout
    trw_dir: Path = checkout.trw_dir  # type: ignore[attr-defined]
    namespace: str = checkout.namespace  # type: ignore[attr-defined]
    client = checkout.client  # type: ignore[attr-defined]

    async def _store() -> dict[str, str]:
        ids = {}
        for label in ("safe", "poison"):
            result = await client.store(f"wal reset corruption recovery {label} path", namespace)
            ids[label] = str(result["memory_id"])
        return ids

    ids = asyncio.run(_store())
    mirror = trw_dir / "learnings" / "entries"
    mirror.mkdir(parents=True, exist_ok=True)
    for label, entry_id in ids.items():
        (mirror / f"{entry_id}.yaml").write_text(
            f'id: "{entry_id}"\nstatus: active\nsummary: "wal reset corruption recovery {label} path"\n',
            encoding="utf-8",
        )
    paths = DaemonPaths.resolve()
    daemon_config = MemoryConfig(storage_path=str(paths.user_memory_dir), memory_single_store_path=str(paths.store))
    ledger_for_config(daemon_config).append(
        LedgerIdentity(namespace=namespace, entry_id=ids["poison"]), "quarantined", actor="test"
    )

    project_root = trw_dir.parent
    _root, hook, _rows = _copy_hook_to_temp(tmp_path / "hook", next(p for p in _HOOK_PATHS if "src" in p.parts))
    completed = subprocess.run(
        ["sh", str(hook)],
        input=json.dumps({"prompt": "wal reset corruption recovery path"}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env={
            **os.environ,
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": "done",
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
            "TRW_PYTHON": sys.executable,
        },
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"[{ids['safe']}]" in completed.stdout or f"[L-{ids['safe']}]" in completed.stdout, completed.stderr
    assert ids["poison"] not in completed.stdout
