"""PRD-CORE-337 FR05 -- every raw write into a checkout from trw-mcp's writer trees is accounted for.

``trw_memory.safe_fs`` (``write_beneath``/``append_beneath``) is the one descriptor-anchored,
symlink-refusing way to write into a project checkout. Charter rule Q2 ("class before site") says the
census lands in the SAME slice as the primitive and BEFORE any call site migrates: it starts green by
listing every current raw write as an audited, class-tagged entry. Adopter slices (FR06-FR10) delete
their own rows as they migrate, which is how this test PROVES a migration instead of merely allowing it.

**Scope.** The four trw-mcp trees FR05 names -- ``bootstrap``, ``channels``, ``state`` and
``models/config`` under ``trw_mcp``. trw-memory's own tree has its own census
(``trw-memory/tests/test_no_raw_checkout_writes_census.py``).

**What counts as a raw write** is decided by the one matcher both censuses import,
``trw_memory._write_census`` (its docstring lists the shapes; its unit tests live in trw-memory's
census). The census has no type checker, so a ``.write_text`` on a ``FileStateWriter`` is counted
too -- that helper is leaf-atomic but walks no parent, so it is a real residual, tagged
``migrates-in-FR10``. Named here, not silently filtered, are the write shapes the matcher does NOT
see: ``os.open`` (flags, not a mode; also how ``safe_fs`` writes), ``tempfile.mkstemp``/
``NamedTemporaryFile`` creation, ``shutil.copy*``/``move`` and ``os.replace`` -- the ``os.fdopen``
that writes through a ``mkstemp`` descriptor IS counted, and its reason names the ``mkstemp``.
Also invisible: writes inside generated script TEXT. ``channels/codex/_post_tool_use_telemetry.py`` and
``channels/antigravity/_before_edit_hook.py`` each embed a hook script (``HOOK_SCRIPT_CONTENT``) whose
body appends telemetry with ``Path.open("a")``; that code runs as the installed hook, not as trw-mcp,
so it is a string here and no AST walk of trw-mcp can count it.

**Keying.** ``(relative path from trw_mcp, enclosing function qualname, 1-based ordinal of the write
within that scope)`` -- the shape of ``test_direct_sqlite_connect_census.py`` -- so an unrelated edit
above a call does not make its row look stale, and a new, unlisted call fails the test by name.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from trw_memory._write_census import CLASS_TAGS, Site, census, raw_write_sites, report

import trw_mcp

#: The trees FR05 audits, relative to the ``trw_mcp`` package directory.
_AUDITED_TREES = ("bootstrap", "channels", "state", "models/config")


#: (relative path, enclosing qualname, 1-based ordinal) -> (class tag, one-line reason). Every row is a
#: REPORTED residual at the class-before-site stage, not a verified-safe exception (tag legend:
#: ``trw_memory._write_census.CLASS_TAGS``).
_AUDITED_WRITES: dict[Site, tuple[str, str]] = {
    ("bootstrap/_update_transaction.py", "_restore_transaction_file", 1): (
        "unscheduled-checkout-write",
        "update rollback: shutil.copy2(follow_symlinks=False) of a snapshot entry back into the checkout after _reject_symlink_path and an unlink; restores links as links, so not a safe_fs write.",
    ),
    ("bootstrap/_update_transaction.py", "_restore_transaction_snapshot", 1): (
        "unscheduled-checkout-write",
        "update rollback: same copy2(follow_symlinks=False) restore as _restore_transaction_file, for a whole snapshot.",
    ),
    ("bootstrap/_update_transaction.py", "_snapshot_transaction_paths", 1): (
        "own-state-stays",
        "copy into a private tempfile.mkdtemp snapshot dir, not the checkout.",
    ),
    ("bootstrap/_update_transaction.py", "run_in_scratch", 1): (
        "own-state-stays",
        "copy into the private scratch tree built by _snapshot_transaction_paths, not the checkout.",
    ),
    ("channels/_manifest_loader.py", "_atomic_dump_yaml", 1): (
        "unscheduled-checkout-write",
        "os.fdopen(fd, 'w') on a tempfile.mkstemp(dir=path.parent) descriptor; mkstemp re-resolves the parent by name (V04).",
    ),
    ("channels/_state.py", "write_state", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp(dir=...) descriptor beside the channel state file; parent re-resolved by name.",
    ),
    ("channels/claude_code/_explorer_subagent.py", "install_cc05_subagent", 1): (
        "unscheduled-checkout-write",
        "claude-code CC05 subagent file under the project; FR07 class, not in FR07's enumerated list.",
    ),
    ("channels/opencode/_custom_commands.py", "install_custom_commands", 1): (
        "unscheduled-checkout-write",
        "opencode custom command files under the project; FR07 class, not in FR07's enumerated list.",
    ),
    ("channels/opencode/_explorer_agent.py", "install_explorer_agent", 1): (
        "unscheduled-checkout-write",
        "opencode explorer agent file under the project; FR07 class, not in FR07's enumerated list.",
    ),
    ("models/config/_credentials.py", "_blank_config_key", 1): (
        "unscheduled-checkout-write",
        ".trw/config.yaml rewrite that blanks a migrated key; plain write_text, not named by FR09.",
    ),
    ("state/_ceremony_progress_state.py", "_emit_nudge_shown_event", 1): (
        "unscheduled-checkout-write",
        ".trw/context/session-events.jsonl append via Path.open('a'); not an R12 row.",
    ),
    ("state/_ceremony_progress_state.py", "write_ceremony_state", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp(dir=path.parent) descriptor for ceremony-state.json under .trw.",
    ),
    ("state/_evidence_persistence.py", "_atomic_write_bytes", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp(dir=target.parent) receipt temp; not an R12 row (row 25, _append_tombstone, was deleted by CORE-313).",
    ),
    ("state/_git_commit_claims.py", "persist_claim", 1): (
        "unscheduled-checkout-write",
        "R12 row 13: deterministic .json.tmp sibling then replace; leaf-atomic but tmp name is predictable and parents unchecked.",
    ),
    ("state/_git_commit_hooks.py", "_snapshot_shared_state", 1): (
        "own-state-stays",
        "writes the observed git index copy inside a private tempfile.TemporaryDirectory, not the checkout.",
    ),
    ("state/_git_commit_hooks.py", "run_blocking_hooks", 1): (
        "own-state-stays",
        "alternates file and COMMIT_EDITMSG inside a private tempfile.TemporaryDirectory, not the checkout.",
    ),
    ("state/_git_commit_hooks.py", "run_blocking_hooks", 2): (
        "own-state-stays",
        "alternates file and COMMIT_EDITMSG inside a private tempfile.TemporaryDirectory, not the checkout.",
    ),
    ("state/_git_commit_workflow_provenance.py", "_persist_prepared_manifest", 1): (
        "unscheduled-checkout-write",
        "deterministic .json.tmp sibling of the prepared manifest in the checkout, then replace; not an R12 row.",
    ),
    ("state/_graph_backfill.py", "_save_state", 1): (
        "unscheduled-checkout-write",
        "R12 row 14: .json.tmp then os.replace under .trw; leaf-atomic, parents unchecked.",
    ),
    ("state/_hook_flags.py", "write_hook_flags", 1): (
        "migrates-in-FR10",
        "receiver is FileStateWriter (mkstemp+replace: leaf-atomic, parent-exposed), not pathlib; FR10 decides its delegation.",
    ),
    ("state/_learn_journal_claims.py", "_publish", 1): (
        "unscheduled-checkout-write",
        "pid/thread-named tmp beside a learn-journal claim under .trw, then linked into place; not an R12 row.",
    ),
    ("state/_persistence_helpers.py", "lock_for_rmw", 1): (
        "unscheduled-checkout-write",
        "sibling .lock file opened 'w' beside a .trw state file; follows a leaf symlink (truncates its target).",
    ),
    ("state/_pin_store.py", "_atomic_write_json", 1): (
        "unscheduled-checkout-write",
        ".trw/runtime pins tmp opened 'w' then replaced; deterministic tmp name.",
    ),
    ("state/_pin_store.py", "_pin_store_file_lock", 1): (
        "unscheduled-checkout-write",
        ".trw/runtime pins lock opened 'a+'; follows a leaf symlink.",
    ),
    ("state/_run_gc_io.py", "_append_event_best_effort", 1): (
        "unscheduled-checkout-write",
        "run events.jsonl append via Path.open('a') in the run dir; not an R12 row.",
    ),
    ("state/_session_changelog.py", "write_session_changelog", 1): (
        "unscheduled-checkout-write",
        "run reports/ session changelog plain write_text in the run dir; not an R12 row.",
    ),
    ("state/_store_migration.py", "_set_pin", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text of .trw/config.yaml; leaf-atomic, parent-exposed; FR10 delegation.",
    ),
    ("state/_store_migration.py", "_write_manifest", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text of the migration manifest; FR10 delegation.",
    ),
    ("state/_tier_sweep.py", "_sweep_cold_to_purge", 1): (
        "unscheduled-checkout-write",
        ".trw/memory/purge_audit.jsonl append via Path.open('a').",
    ),
    ("state/acceptance_manifest.py", "persist_manifest", 1): (
        "unscheduled-checkout-write",
        "mkstemp(dir=parent) tmp re-opened by name via write_text, then replaced; parents unchecked.",
    ),
    ("state/analytics/_stale_runs.py", "_save_persisted_throttle", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp(dir=path.parent) descriptor for the auto-close throttle file.",
    ),
    ("state/claude_md/_instruction_carrier.py", "heal_pointer", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text of an instruction file; FR10 delegation.",
    ),
    ("state/claude_md/_orphan_strip.py", "_strip_orphaned_block", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text of an instruction file; FR10 delegation.",
    ),
    ("state/claude_md/_sync.py", "generate_review_md", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp descriptor for REVIEW.md in the checkout.",
    ),
    ("state/claude_md/_write_backup.py", "backup_instruction_file", 1): (
        "unscheduled-checkout-write",
        "timestamped instruction-file backup under the project's backup dir; plain write_text.",
    ),
    ("state/claude_md/_write_guard.py", "guarded_instruction_write", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text behind the instruction write guard; FR10 delegation.",
    ),
    ("state/doc_variants.py", "write_variant", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on an O_EXCL descriptor opened by path for a doc-variant file; parents unchecked.",
    ),
    ("state/git_commit_transaction.py", "write_journal", 1): (
        "unscheduled-checkout-write",
        "R12 row 8: mkstemp tmp re-opened by name, then replaced; leaf-atomic, parents unchecked.",
    ),
    ("state/index_sync.py", "sync_index_md", 1): (
        "migrates-in-FR10",
        "writer: FileStateWriter renders the PRD INDEX.md; FR10 delegation.",
    ),
    ("state/index_sync.py", "sync_roadmap_md", 1): (
        "migrates-in-FR10",
        "writer: FileStateWriter renders ROADMAP.md; FR10 delegation.",
    ),
    ("state/knowledge_topology.py", "_write_knowledge_files", 1): (
        "migrates-in-FR10",
        "writer: FileStateWriter renders knowledge topic files; FR10 delegation.",
    ),
    ("state/knowledge_topology.py", "execute_knowledge_sync", 1): (
        "unscheduled-checkout-write",
        "open() of the mkstemp(dir=output_dir) DESCRIPTOR, not a path; the mkstemp itself re-resolves output_dir (V04).",
    ),
    ("state/nudge_analysis.py", "persist_nudge_analysis", 1): (
        "unscheduled-checkout-write",
        "os.fdopen on a mkstemp(dir=path.parent) descriptor for nudge-analysis.json under .trw.",
    ),
    ("state/persistence.py", "FileStateWriter.append_jsonl", 1): (
        "migrates-in-FR10",
        "FileStateWriter's own append path: path.open('a') follows a leaf symlink; FR10's named helper.",
    ),
    ("state/persistence.py", "_atomic_write_text_file", 1): (
        "migrates-in-FR10",
        "FileStateWriter's leaf publish: os.fdopen on a mkstemp(dir=path.parent) descriptor, then replace; FR10's named helper.",
    ),
    ("state/prd_sections.py", "update_execution_plan", 1): (
        "migrates-in-FR10",
        "FileStateWriter().write_text of a PRD file; FR10 delegation.",
    ),
    ("state/prd_utils.py", "update_frontmatter", 1): (
        "unscheduled-checkout-write",
        "R12 row 18: mkstemp tmp re-opened by name, then renamed; parents unchecked.",
    ),
    ("state/pre_compact_marker.py", "write_pre_compact_marker", 1): (
        "unscheduled-checkout-write",
        "pid-named tmp beside the pre-compact marker under .trw, then replaced.",
    ),
    ("state/recall_tracking.py", "_append_rows", 1): (
        "unscheduled-checkout-write",
        ".trw recall-tracking JSONL append via Path.open('a').",
    ),
    ("state/requirements_registry.py", "RegistryWriter._append_locked", 1): (
        "unscheduled-checkout-write",
        "registry ledger append via Path.open('a'); the render (persist_registry) is R12 row 19, this append is not.",
    ),
    ("state/surface_tracking.py", "log_surface_event", 1): (
        "unscheduled-checkout-write",
        ".trw logs surface-event JSONL append via Path.open('a').",
    ),
    ("state/tiers.py", "TierManager._warm_sidecar_upsert", 1): (
        "unscheduled-checkout-write",
        ".trw/memory/warm.jsonl plain rewrite.",
    ),
    ("state/_store_migration.py", "apply_migration", 1): (
        "unscheduled-checkout-write",
        ".trw/memory backup copied to a fresh migration-work dir with shutil.copyfile (counted since the matcher learned shutil copies).",
    ),
    ("state/tiers.py", "TierManager.warm_remove", 1): (
        "unscheduled-checkout-write",
        ".trw/memory/warm.jsonl plain rewrite.",
    ),
}


def _sites(package_root: Path | None = None) -> list[Site]:
    """Every raw write under the audited trees of *package_root* (default: the imported ``trw_mcp``)."""
    return raw_write_sites(package_root or Path(trw_mcp.__file__).parent, _AUDITED_TREES)


def _copy_audited_trees(destination: Path) -> Path:
    source = Path(trw_mcp.__file__).parent
    for tree_name in _AUDITED_TREES:
        shutil.copytree(source / tree_name, destination / tree_name, ignore=shutil.ignore_patterns("__pycache__"))
    return destination


def test_every_audited_tree_exists() -> None:
    """A renamed tree would silently shrink the census to nothing; the scope is pinned by existence."""
    root = Path(trw_mcp.__file__).parent
    assert [tree for tree in _AUDITED_TREES if not (root / tree).is_dir()] == []


def test_every_raw_checkout_write_is_accounted_for() -> None:
    """FR05: a new, unlisted raw write fails by path, qualname and ordinal -- route it through ``safe_fs``."""
    unlisted, _stale = census(_sites(), _AUDITED_WRITES)
    assert unlisted == [], (
        report(unlisted, [])
        + "\nwrite it with trw_memory.safe_fs.write_beneath/append_beneath, or add a class-tagged row"
    )


def test_every_allowlist_row_still_has_its_call_site() -> None:
    """A migrated or moved site must take its row with it: the allowlist tracks source, it does not fossilize."""
    _unlisted, stale = census(_sites(), _AUDITED_WRITES)
    assert stale == [], report([], stale)


def test_every_allowlist_row_is_class_tagged_with_a_one_line_reason() -> None:
    bad = [
        key
        for key, (tag, reason) in _AUDITED_WRITES.items()
        if tag not in CLASS_TAGS or not reason.strip() or "\n" in reason
    ]
    assert bad == []


@pytest.mark.parametrize(
    "planted",
    ["target.write_text('planted')", "builtins.open(target, 'w')", "open(target, **options)"],
)
def test_a_planted_write_in_cursor_py_turns_the_census_red_by_name(tmp_path: Path, planted: str) -> None:
    """Guard-the-guard, on a COPY of the real trees: one planted write is the only failure, named exactly."""
    root = _copy_audited_trees(tmp_path)
    with (root / "bootstrap" / "_cursor.py").open("a", encoding="utf-8") as handle:
        handle.write(f"\n\ndef _planted_census_probe(target, options):\n    {planted}\n")

    unlisted, stale = census(_sites(root), _AUDITED_WRITES)

    assert unlisted == [("bootstrap/_cursor.py", "_planted_census_probe", 1)]
    assert stale == []
    assert "bootstrap/_cursor.py :: _planted_census_probe #1" in report(unlisted, stale)


def test_dropping_an_allowlist_row_without_its_call_site_turns_the_census_red() -> None:
    if not _AUDITED_WRITES:
        pytest.skip("every audited write has migrated; there is no row left to drop")
    dropped = min(_AUDITED_WRITES)
    remaining = {key: value for key, value in _AUDITED_WRITES.items() if key != dropped}

    unlisted, stale = census(_sites(), remaining)

    assert unlisted == [dropped]
    assert stale == []


def test_deleting_a_call_site_but_keeping_its_row_turns_the_census_red(tmp_path: Path) -> None:
    if not _AUDITED_WRITES:
        pytest.skip("every audited write has migrated; there is no row to go stale")
    root = _copy_audited_trees(tmp_path)
    victim = min(_AUDITED_WRITES)[0]
    (root / victim).unlink()

    unlisted, stale = census(_sites(root), _AUDITED_WRITES)

    assert unlisted == []
    assert stale == sorted(key for key in _AUDITED_WRITES if key[0] == victim)


def test_the_bootstrap_tree_has_no_allowlisted_raw_write() -> None:
    """PRD-CORE-337 FR06: every init/update writer goes through ``trw_mcp._checkout_write``.

    The live ``.cursor/hooks.json`` writer (``_cursor_hooks_io.smart_merge_cursor_json``) stayed raw
    after FR06 because its rows were tagged ``unscheduled-checkout-write``: the census counted it, and
    the tag let it pass, and its hook scripts were a ``shutil.copy2`` the matcher did not count. No
    bootstrap writer may be grandfathered again: the residual set is pinned, and a new write fails by name.
    """
    residual = sorted(
        key
        for key, (tag, _reason) in _AUDITED_WRITES.items()
        if key[0].startswith("bootstrap/") and tag == "unscheduled-checkout-write"
    )
    # Only the update rollback, which restores snapshot symlinks as symlinks, is left raw.
    assert residual == [
        ("bootstrap/_update_transaction.py", "_restore_transaction_file", 1),
        ("bootstrap/_update_transaction.py", "_restore_transaction_snapshot", 1),
    ]


def test_a_planted_write_in_the_live_cursor_hooks_writer_turns_the_census_red(tmp_path: Path) -> None:
    root = _copy_audited_trees(tmp_path)
    with (root / "bootstrap" / "_cursor_hooks_io.py").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\ndef _planted_census_probe(target, src):\n    target.write_text('planted')\n    shutil.copy2(src, target)\n"
        )

    unlisted, stale = census(_sites(root), _AUDITED_WRITES)

    assert unlisted == [
        ("bootstrap/_cursor_hooks_io.py", "_planted_census_probe", 1),
        ("bootstrap/_cursor_hooks_io.py", "_planted_census_probe", 2),
    ]
    assert stale == []


# --- extension: FR08 writers outside the four audited trees stay migrated (PRD-CORE-337 FR08) -------------------------

#: (path from trw_mcp, qualname) of every FR08 writer outside ``bootstrap``/``channels``/``state``/
#: ``models/config``. The FR05 census deletes the in-tree rows; these trees it does not walk.
_MIGRATED_OUTSIDE_CENSUS = frozenset(
    {
        ("security/_anomaly_state.py", "_ensure_shadow_clock"),  # R12 row 3 (moved out of anomaly_detector.py on int)
        ("meta_tune/promote.py", "_run_after_staging"),  # R12 rows 5, 6
        ("meta_tune/promote_helpers.py", "write_promotion_backup"),  # R12 row 6's backup (was a shutil.copy2)
        ("server/_subcommands.py", "_run_audit"),  # the twin of R12 row 7
        ("server/_subcommands.py", "_run_export"),  # R12 row 7
        ("telemetry/sender.py", "BatchSender._rewrite_queue"),  # R12 row 11
        ("tools/_post_commit.py", "_mark_pending"),  # R12 row 22
        ("tools/_post_commit.py", "_write_receipt"),  # R12 row 22's sibling receipt
    }
)


def test_fr08_writers_outside_the_census_trees_have_no_raw_write() -> None:
    trees = sorted({path.split("/", 1)[0] for path, _ in _MIGRATED_OUTSIDE_CENSUS})
    raw = {(path, qualname) for path, qualname, _ in raw_write_sites(Path(trw_mcp.__file__).parent, trees)}
    assert raw & _MIGRATED_OUTSIDE_CENSUS == set(), "write it with trw_mcp._checkout_write"


def test_the_census_extension_sees_a_reverted_writer(tmp_path: Path) -> None:
    """Guard-the-guard: a raw write put back into one migrated function is found by name."""
    source = Path(trw_mcp.__file__).parent / "telemetry" / "sender.py"
    copy = tmp_path / "telemetry" / "sender.py"
    copy.parent.mkdir()
    copy.write_text(
        source.read_text(encoding="utf-8").replace(
            "        write_checkout_file(self._input_path.parent, self._input_path, lines)\n",
            "        self._input_path.write_text(lines, encoding='utf-8')\n",
        ),
        encoding="utf-8",
    )
    raw = {(path, qualname) for path, qualname, _ in raw_write_sites(tmp_path, ("telemetry",))}
    assert ("telemetry/sender.py", "BatchSender._rewrite_queue") in raw
