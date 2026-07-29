"""The registry of everything that may write a ``content_hashes`` key.

Belongs to the ``_version_migration.py`` facade (``_write_manifest``).

Why this module exists (PRD-FIX-121-FR05)
-----------------------------------------
``content_hashes`` in ``.trw/managed-artifacts.yaml`` means **"what TRW last
wrote"**. It is the second baseline ``_version_manifest._is_user_modified``
consults, so whatever produces it is part of the user-edit guard, not a
bookkeeping detail.

Three functions used to contribute keys to that map and only one of them
declined to record an artifact the user had edited. The other two recorded
whatever was on disk — including an edit the update had just finished
*preserving* — so on the next run the guard read the user's own hash back as
TRW's baseline and overwrote the file. Measured: preserved on run 1, destroyed
on run 2, silently (``result['modified']`` was empty that time). Seven surfaces
were affected; the two that were immune were exactly the two routed through the
one recorder that declined. That is wiring-defect class P10 — *hardened once,
copied N times* — and the fix for P10 is never three more edits, because a
fourth recorder undoes them.

So the recorder set is **declared here and iterated**, rather than being three
calls inlined into :func:`_write_manifest`:

- adding a recorder without registering it means it is never called at all,
  which is a loud failure instead of a silent ownership leak;
- ``tests/test_bootstrap_manifest_ownership.py::TestNoUnguardedRecorder``
  AST-checks that ``_write_manifest`` derives ``content_hashes`` from this
  registry and from nothing else, and behaviourally checks that every member
  declines a synthetic user edit.

What this module deliberately does NOT own
------------------------------------------
Only the ``content_hashes`` map. The rest of the manifest — including keys
written by other subsystems — is not this registry's business, and nothing here
asserts what the manifest's top-level key set is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: A recorder is called with ``(target_dir, prev_hashes, data_dir)`` and returns
#: the ``content_hashes`` entries it owns. *prev_hashes* is the manifest as it
#: stood BEFORE this run — the only thing that can distinguish "stale but mine"
#: from "the user edited it".
RecorderFn = Callable[[Path, "dict[str, str] | None", "Path | None"], "dict[str, str]"]


@dataclass(frozen=True, slots=True)
class ManifestRecorder:
    """One contributor of ``content_hashes`` keys.

    Attributes:
        name: Stable identifier used in logs and by the FR05 totality test.
        surfaces: Repo-relative surfaces the recorder owns (diagnostics only).
        record: The recorder itself. MUST omit any artifact for which
            ``_managed_client_artifacts.artifact_user_edited`` is true.
    """

    name: str
    surfaces: tuple[str, ...]
    record: RecorderFn


def _record_core_artifacts(
    target_dir: Path,
    prev_hashes: dict[str, str] | None,
    data_dir: Path | None,
) -> dict[str, str]:
    """``.claude/{agents,hooks,skills}``, ``.opencode/**`` and the two INSTRUCTIONS.md."""
    from ._template_updater import _get_bundled_names
    from ._version_manifest import _compute_content_hashes

    return _compute_content_hashes(target_dir, _get_bundled_names(data_dir), prev_hashes, data_dir)


def _record_codex_artifacts(
    target_dir: Path,
    prev_hashes: dict[str, str] | None,
    data_dir: Path | None,
) -> dict[str, str]:
    """``.codex/agents/*.toml`` and ``.agents/skills/**``.

    *data_dir* is unused: the codex mirrors are generated from in-repo templates
    and the codex skills source dir, neither of which the ``data_dir`` override
    reaches.
    """
    from ._version_migration_clients import _codex_manifest_hashes

    return _codex_manifest_hashes(target_dir, prev_hashes)


def _record_managed_client_artifacts(
    target_dir: Path,
    prev_hashes: dict[str, str] | None,
    data_dir: Path | None,
) -> dict[str, str]:
    """``.github/**``, ``.cursor/**`` and ``.antigravitycli/agents``.

    *data_dir* is unused: these surfaces are built from per-generator content
    callables (``MANAGED_CLIENT_ARTIFACT_SOURCES``), not from the data directory.
    """
    from ._managed_client_artifacts import managed_client_manifest_hashes

    return managed_client_manifest_hashes(target_dir, prev_hashes)


MANIFEST_RECORDERS: tuple[ManifestRecorder, ...] = (
    ManifestRecorder(
        "core_artifacts",
        (".claude/agents", ".claude/hooks", ".claude/skills", ".opencode", ".codex/INSTRUCTIONS.md"),
        _record_core_artifacts,
    ),
    ManifestRecorder("codex_artifacts", (".codex/agents", ".agents/skills"), _record_codex_artifacts),
    ManifestRecorder(
        "managed_client_artifacts",
        (".github/agents", ".github/instructions", ".github/skills", ".antigravitycli/agents", ".cursor"),
        _record_managed_client_artifacts,
    ),
)


def collect_manifest_content_hashes(
    target_dir: Path,
    prev_hashes: dict[str, str] | None,
    data_dir: Path | None = None,
) -> dict[str, str]:
    """Build the whole ``content_hashes`` map from the declared recorder registry.

    A recorder that raises is logged and skipped. Losing a recorder's keys costs
    refresh precision on its surfaces; recording an artifact TRW does not own
    costs the user their work. Only one of those is recoverable, so the failure
    direction is omission (PRD-FIX-121-NFR01/NFR04).
    """
    # Named ``content_hashes`` deliberately: the FR05 AST guard keys on that
    # name in both this function and ``_write_manifest``, so every contributor to
    # the map is visible to the same check.
    content_hashes: dict[str, str] = {}
    for recorder in MANIFEST_RECORDERS:
        try:
            content_hashes.update(recorder.record(target_dir, prev_hashes, data_dir))
        except Exception:  # justified: a broken recorder must not abort the manifest write
            logger.warning("manifest_recorder_failed", recorder=recorder.name, exc_info=True)
    return content_hashes
