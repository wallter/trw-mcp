"""PRD-CORE-316 FR07 -- every checkout-file open in trw-mcp goes through ``_checkout_access.py``.

Charter rule Q2 ("class before site"): this census lands in Slice A, before any call site
migrates onto the consolidated module, so a future direct open never slips back in unnoticed.
It fails on two independent classes of AST-visible site:

1. A module inside ``trw_mcp/code_index/``, ``trw_mcp/comms/_upgrade.py`` or
   ``trw_mcp/tools/_delivery_journal_store.py`` calling ``os.open``, ``os.pread`` or builtin
   ``open`` directly, instead of importing from ``trw_mcp._checkout_access`` -- UNLESS that exact
   module is on ``_ALLOWLIST`` with a one-line reason.
2. Any ``os.open`` call carrying the ``O_NOFOLLOW`` flag anywhere in ``trw-mcp/src``, outside
   ``_checkout_access.py`` itself -- UNLESS that module is on ``_ALLOWLIST``.

``code_index/discovery.py``'s ``_open_under``/``read_indexed_file`` carried a transitional
allowlist row through Slice A (they still opened files directly at that point). PRD-CORE-316
Slice B deleted that row when ``read_indexed_file``/``_skip_as_binary`` were rewired onto
``_checkout_access.open_under`` -- the census below now proves that migration by failing red if a
direct open in that module ever reappears.
"""

from __future__ import annotations

import ast
from pathlib import Path

import trw_mcp

#: Repo-relative path (from ``trw_mcp/src/trw_mcp``) -> one-line, dated reason. Every entry is a
#: named residual, not a silently-tolerated one; each states whether it is own-state (stays) or a
#: consolidation candidate (a backlog row).
_ALLOWLIST: dict[str, str] = {
    "server/_uninstall_quiesce.py": (
        "UNINSTALL-DISTILL-RACE: opens trw-mcp's own writer lock files (post-commit, incremental, sidecar-rebuild) "
        "O_NOFOLLOW relative to the .trw directory fd uninstall already holds, only to flock them; no byte is "
        "read from them, and a link planted at a lock path is refused by O_NOFOLLOW. Own-state, not migrated."
    ),
    "bootstrap/_managed_dirs.py": (
        "PRD-FIX pruned-dir probe: opens a directory O_NOFOLLOW only to lstat its `.git` entry relative "
        "to that descriptor (a symlink is never followed); the only bytes read are the first 4 KiB of "
        "a regular-file `.git` (lstat-checked, never a link) to recognise a submodule `gitdir:` line; "
        "a consolidation candidate, not migrated here."
    ),
    "code_index/storage.py": (
        "PRD-CORE-316: own-state -- the code index's own manifest/store files (this process's own "
        "build output), not checkout-supplied content; a consolidation candidate, not migrated here."
    ),
    "state/_evidence_fs.py": (
        "PRD-CORE-316: own-state -- the evidence store's own anchored walk over trw-mcp-owned "
        "receipt content; a consolidation candidate, not migrated here."
    ),
    "state/_evidence_identity.py": (
        "PRD-CORE-316: own-state -- opens the evidence root to derive a stable identity, not a read "
        "of checkout-supplied content; a consolidation candidate, not migrated here."
    ),
    "security/intent_contract/paths.py": (
        "PRD-CORE-316: own-state -- intent-contract path classification and writes for trw-mcp's own "
        "security subsystem; a consolidation candidate, not migrated here."
    ),
    "security/intent_contract/_atomic_json.py": (
        "PRD-CORE-316: own-state -- atomic write of trw-mcp's own intent-contract state file "
        "(O_NOFOLLOW on the final component only); a consolidation candidate, not migrated here."
    ),
    "security/intent_contract/break_glass.py": (
        "PRD-CORE-316: own-state -- creates trw-mcp's own break-glass marker file (O_CREAT|O_EXCL|"
        "O_NOFOLLOW); a consolidation candidate, not migrated here."
    ),
    "state/_verification_artifact.py": (
        "PRD-CORE-316: own-state -- an anchored, component-wise O_NOFOLLOW walk over trw-mcp's own "
        "verification-artifact tree (the same pattern as state/_evidence_fs.py); a consolidation "
        "candidate, not migrated here."
    ),
    "comms/_upgrade.py": (
        "PRD-CORE-316: own-state residual -- _fsync_file_and_dir opens the just-written backup's own "
        "parent directory (os.open(path.parent, O_DIRECTORY)) purely to fsync it for durability; it "
        "reads no checkout content and is unrelated to this file's read_at/copy_to imports (already "
        "migrated onto _checkout_access for FR01/FR02)."
    ),
    "server/_uninstall_corpus.py": (
        "own-state -- uninstall opens TRW's own .trw dir O_NOFOLLOW as the anchor for removing its "
        "children; a directory handle for unlink/rmtree, never a read of checkout-supplied content."
    ),
    "bootstrap/_retire.py": (
        "own-state -- retire_file hashes a stale TRW artifact through an O_NOFOLLOW|O_NONBLOCK fd "
        "(size-capped) before deleting it in place; no checkout content is read into TRW state."
    ),
    "bootstrap/_trash.py": (
        "own-state -- remove_if_hash anchors dir fds for rename/link and reads/writes only TRW's own "
        "trash capture (meta.json, data); _checkout_access pinning would hold fds on captured user bytes."
    ),
    "bootstrap/_proven_replace.py": (
        "own-state -- replace_proven writes the replacement bytes into TRW's own staging folder in .trw/trash "
        "through an O_NOFOLLOW fd and links it at the name; it never reads checkout content (CLAUDE-MD S2)."
    ),
    "bootstrap/_trash_purge.py": (
        "own-state -- uninstall re-reads only TRW's own trash capture (meta.json, data) through O_NOFOLLOW fds to "
        "prove it unchanged before deleting it; no checkout content enters TRW state."
    ),
}

#: Modules whose direct opens are censused unless allowlisted (FR07 rule 1).
_CENSUSED_PREFIXES: tuple[str, ...] = ("code_index/",)
_CENSUSED_FILES: frozenset[str] = frozenset({"comms/_upgrade.py", "tools/_delivery_journal_store.py"})

_MODULE_FILE = "_checkout_access.py"


def _is_censused_path(relative: str) -> bool:
    return relative in _CENSUSED_FILES or any(relative.startswith(prefix) for prefix in _CENSUSED_PREFIXES)


def _calls_direct_open(tree: ast.Module) -> bool:
    """True if *tree* calls ``os.open``, ``os.pread`` or builtin ``open`` anywhere."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            return True
        if isinstance(func, ast.Attribute) and func.attr in {"open", "pread"}:
            receiver = func.value
            if isinstance(receiver, ast.Name) and receiver.id == "os":
                return True
    return False


def _calls_o_nofollow_open(tree: ast.Module) -> bool:
    """True if any ``os.open(...)`` call in *tree* carries ``O_NOFOLLOW`` among its flag arguments."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open"):
            continue
        receiver = node.func.value
        if not (isinstance(receiver, ast.Name) and receiver.id == "os"):
            continue
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Attribute) and sub.attr == "O_NOFOLLOW":
                    return True
    return False


def _census_violations(root: Path | None = None) -> list[tuple[str, str]]:
    """`(relative_path, rule)` for every unallowlisted direct-open site under *root*."""
    package_root = root if root is not None else Path(trw_mcp.__file__).parent
    violations: list[tuple[str, str]] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = str(path.relative_to(package_root).as_posix())
        if relative == _MODULE_FILE:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # trw-fail-silent-allow: an unparseable file under trw_mcp/src fails ruff/mypy first
            continue
        if relative in _ALLOWLIST:
            continue
        if _is_censused_path(relative) and _calls_direct_open(tree):
            violations.append((relative, "direct-open-in-censused-module"))
        elif _calls_o_nofollow_open(tree):
            violations.append((relative, "o-nofollow-open-outside-module"))
    return violations


def test_no_unallowlisted_checkout_file_open_outside_the_module() -> None:
    """FR07: a new, unlisted direct open (or O_NOFOLLOW open) fails this test by name."""
    violations = _census_violations()
    assert violations == [], (
        f"unallowlisted checkout-file open(s) found: {violations} -- route through "
        "trw_mcp._checkout_access, or add a one-line reasoned _ALLOWLIST row."
    )


def test_every_allowlist_row_still_exists() -> None:
    """The allowlist must track the source: a moved/removed/fixed module is stale, not safe to keep."""
    package_root = Path(trw_mcp.__file__).parent
    stale = [relative for relative in _ALLOWLIST if not (package_root / relative).is_file()]
    assert stale == [], f"_ALLOWLIST entries with no matching file: {stale}"


def test_discovery_no_longer_needs_its_transitional_allowlist_row() -> None:
    """PRD-CORE-316 Slice B: proves the migration the deleted allowlist row promised.

    ``code_index/discovery.py`` is no longer allowlisted (see ``_ALLOWLIST`` above) and no longer
    opens files directly -- ``read_indexed_file``/``_skip_as_binary`` delegate to
    ``_checkout_access.open_under``. If a direct open ever reappears here, both this test and
    ``test_no_unallowlisted_checkout_file_open_outside_the_module`` fail (Q2's census keeps proving
    the migration, not just asserting it once).
    """
    package_root = Path(trw_mcp.__file__).parent
    assert "code_index/discovery.py" not in _ALLOWLIST
    tree = ast.parse((package_root / "code_index/discovery.py").read_text())
    assert not _calls_direct_open(tree), (
        "code_index/discovery.py opens a checkout file directly again -- route it through "
        "trw_mcp._checkout_access.open_under (PRD-CORE-316 FR07), or re-add a reasoned _ALLOWLIST row."
    )


def test_walker_catches_an_injected_direct_open_in_a_censused_module(tmp_path: Path) -> None:
    """Guard-the-guard: an unlisted direct ``os.open`` in a censused module must be caught."""
    package = tmp_path / "throwaway_pkg"
    (package / "code_index").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "code_index" / "__init__.py").write_text("")
    (package / "code_index" / "_new_module.py").write_text(
        "import os\n\n\ndef read_it(path):\n    fd = os.open(path, os.O_RDONLY)\n    return fd\n"
    )

    violations = _census_violations(root=package)

    assert ("code_index/_new_module.py", "direct-open-in-censused-module") in violations


def test_walker_catches_an_injected_o_nofollow_open_outside_the_module(tmp_path: Path) -> None:
    """Guard-the-guard: an O_NOFOLLOW open outside ``_checkout_access.py`` and outside a censused dir is still caught."""
    package = tmp_path / "throwaway_pkg2"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_unrelated_module.py").write_text(
        "import os\n\n\ndef open_it(path):\n    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)\n"
    )

    violations = _census_violations(root=package)

    assert ("_unrelated_module.py", "o-nofollow-open-outside-module") in violations


def test_walker_ignores_unrelated_open_and_pread_lookalikes(tmp_path: Path) -> None:
    """A ``.open(`` / ``.pread(`` call on something that is not the ``os`` module must not false-positive."""
    package = tmp_path / "throwaway_pkg3"
    (package / "code_index").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "code_index" / "__init__.py").write_text("")
    (package / "code_index" / "_new_module.py").write_text(
        "class Handle:\n    def open(self):\n        return self\n\n\ndef use_it(handle):\n    return handle.open()\n"
    )

    assert _census_violations(root=package) == []


def test_module_itself_is_exempt_from_its_own_census() -> None:
    """``_checkout_access.py`` is the one place direct opens are expected; the census must not flag it."""
    package_root = Path(trw_mcp.__file__).parent
    violations = _census_violations()
    assert not any(relative == _MODULE_FILE for relative, _rule in violations)
    assert (package_root / _MODULE_FILE).is_file()
