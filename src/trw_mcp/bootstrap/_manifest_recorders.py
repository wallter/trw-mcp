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
        surfaces: Repo-relative directories (or single files) holding every key the recorder can write; a part
            may be an ``fnmatch`` pattern (``.cursor/skills/trw-*``). They gate behaviour: a deletion tombstone is
            honoured only under one of them (TOMBSTONE-TRW-KEYS-ONLY), so they name what TRW writes and no more --
            a directory the user also writes to (``.cursor``, ``.opencode``) is listed by its ``trw-*`` entries and
            exact files. ``tests/test_bootstrap_tombstones.py`` checks every key a recorder can write lies under one.
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
        (
            ".claude/agents",
            ".claude/hooks",
            ".claude/skills",
            ".codex/INSTRUCTIONS.md",
            ".opencode/INSTRUCTIONS.md",
            ".opencode/commands/trw-*",
            ".opencode/skills/trw-*",
        ),
        _record_core_artifacts,
    ),
    ManifestRecorder("codex_artifacts", (".codex/agents", ".agents/skills"), _record_codex_artifacts),
    ManifestRecorder(
        "managed_client_artifacts",
        (
            ".agents/agents",
            ".agents/hooks",
            ".agents/rules",
            ".antigravitycli/agents",
            ".claude/agents",
            ".claude/hooks",
            ".claude/loop.md",
            ".codex/agents",
            ".codex/hooks",
            ".cursor/agents/trw-*",
            ".cursor/cli.json",
            ".cursor/commands/trw-*",
            ".cursor/hooks/_nudge_gate.py",
            ".cursor/hooks/cli-adapter.sh",
            ".cursor/hooks/lib-distill-hint.sh",
            ".cursor/hooks/trw-*",
            ".cursor/rules/trw-*",
            ".cursor/skills/trw-*",
            ".github/agents",
            ".github/hooks",
            ".github/instructions",
            ".github/skills",
            ".grok/agents",
            ".opencode/agents/trw-*",
            ".opencode/commands/trw-*",
        ),
        _record_managed_client_artifacts,
    ),
)


def under_recorder_surface(rel: str, surfaces: tuple[str, ...] | None = None) -> bool:
    """Whether repo-relative *rel* lies under one of *surfaces* (default: every recorder's).

    Compared part by part (a surface part may be an ``fnmatch`` pattern), and a path with an empty, ``.`` or
    ``..`` part is never under one: a key such as ``.claude/hooks/../../CLAUDE.md`` or ``./CLAUDE.md`` names a
    file no recorder writes.
    """
    from fnmatch import fnmatchcase

    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    declared = surfaces if surfaces is not None else tuple(s for r in MANIFEST_RECORDERS for s in r.surfaces)
    for surface in declared:
        patterns = surface.split("/")
        if len(parts) >= len(patterns) and all(map(fnmatchcase, parts, patterns)):
            return True
    return False


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


def dropped_manifest_keys(
    target_dir: Path, prev_hashes: dict[str, str] | None, content_hashes: dict[str, str]
) -> list[str]:
    """One ``manifest_key_dropped`` warning per prior key whose file survives unrecorded (PRD-INFRA-190 FR07).

    A recorder omits a key for exactly two reasons, and the bytes tell them
    apart: content that moved off TRW's own last write is ``user_edited``;
    content still equal to that write lost its framework baseline
    (``baseline_unavailable``). Silence here is how eleven agent hashes vanished.
    """
    import hashlib

    from ._version_manifest import _manifest_key_path

    warnings: list[str] = []
    for key, recorded in sorted((prev_hashes or {}).items()):
        path = target_dir / _manifest_key_path(key)
        if key in content_hashes or not path.is_file():
            continue
        try:
            edited = hashlib.sha256(path.read_bytes()).hexdigest() != recorded
        except OSError:  # an unreadable file cannot be hashed, so it has no baseline either
            edited = False
        reason = "user_edited" if edited else "baseline_unavailable"
        warnings.append(f"manifest_key_dropped: {key} ({reason})")
    return warnings
