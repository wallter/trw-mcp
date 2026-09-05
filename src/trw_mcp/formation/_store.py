"""Manifest location, the pointer index, and the locked read-modify-write.

CANONICAL LOCATION (FR01). Exactly one path holds formation ownership:
``<orchestrator run dir>/formation.yaml``. It sits beside the shards directory
the wave manifest already uses, so a formation's artifacts stay inside the run
that owns them and are garbage-collected with it.

THE POINTER INDEX, AND WHY IT IS NOT A SECOND OWNERSHIP PATH. A member's run
directory is not the orchestrator's, and a member run records only
``formation_id`` (FR04). Something must turn that id into the manifest's path
without scanning every run on disk (~200 here, PyYAML-parsed — the cost
``resolve_run_path``'s HOT-PATH RULE exists to avoid). That is
``<trw_dir>/runtime/formations.json``: a map from ``formation_id`` to the
orchestrator run path, written only by this module. It carries no member, no
glob, no status — it is an INDEX, not an ownership artifact, which is why FR02's
"exactly one ownership path" still holds. Losing it costs discovery, never
authority: the manifest remains the only thing anything reads ownership from.

FAIL-CLOSED, AND THE DISTINCTION THAT MATTERS (NFR02). ``resolve_active``
returns ``None`` for "there is no formation" and RAISES
:class:`~trw_mcp.formation._manifest.FormationError` for "there is a formation
and I could not read it". Conflating those two is the reassuring-fallback defect
in ``docs/documentation/wiring-defect-patterns.md`` — the same defect FR02 is
repairing in ``checkpoint.py`` — so every adapter gets two distinguishable
outcomes and never one permissive one.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.formation._manifest import FormationError, FormationManifest

logger = structlog.get_logger(__name__)

__all__ = [
    "MANIFEST_FILENAME",
    "FormationContext",
    "manifest_path_for_run",
    "read_manifest",
    "register_formation",
    "resolve_active",
    "resolve_manifest_path",
    "rewrite_manifest",
    "write_manifest_locked",
]

#: The one file name. Never construct this string anywhere else.
MANIFEST_FILENAME = "formation.yaml"

_INDEX_RELATIVE = ("runtime", "formations.json")
_LOCK_SUFFIX = ".lock"
_LOCK_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class FormationContext:
    """The formation a given run belongs to, and that run's place in it."""

    manifest: FormationManifest
    manifest_path: Path
    #: ``None`` when the resolving run is the orchestrator itself.
    member_id: str | None
    is_orchestrator: bool


def manifest_path_for_run(run_path: Path) -> Path:
    """Canonical manifest path for an orchestrator *run_path*."""
    return run_path / MANIFEST_FILENAME


def _index_path(trw_dir: Path) -> Path:
    return trw_dir.joinpath(*_INDEX_RELATIVE)


def _read_index(trw_dir: Path) -> dict[str, str]:
    path = _index_path(trw_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FormationError(f"formation index {path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise FormationError(f"formation index {path} is not an object")
    return {str(k): str(v) for k, v in raw.items()}


def register_formation(trw_dir: Path, formation_id: str, orchestrator_run_path: Path) -> None:
    """Record ``formation_id -> orchestrator run path`` in the pointer index."""
    path = _index_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive(path):
        index = _read_index(trw_dir)
        index[formation_id] = str(orchestrator_run_path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def resolve_manifest_path(trw_dir: Path, formation_id: str) -> Path:
    """Manifest path for *formation_id*, or raise naming the known ids."""
    index = _read_index(trw_dir)
    recorded = index.get(formation_id)
    if recorded is None:
        known = ", ".join(sorted(index)) or "(none)"
        raise FormationError(f"unknown formation_id {formation_id!r}. Registered: {known}")
    return manifest_path_for_run(Path(recorded))


def read_manifest(manifest_path: Path) -> FormationManifest:
    """Parse and validate the manifest at *manifest_path*.

    Raises :class:`FormationError` naming the file and the parse error on every
    failure mode — absent, unreadable, truncated, or schema-invalid. Callers
    that must distinguish "absent" use :func:`resolve_active`, which asks
    whether the file exists BEFORE calling this.
    """
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FormationError(f"formation manifest {manifest_path} is unreadable: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FormationError(f"formation manifest {manifest_path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise FormationError(f"formation manifest {manifest_path} must contain a mapping, got {type(data).__name__}")
    try:
        return FormationManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError, or a FormationError from a glob
        raise FormationError(f"formation manifest {manifest_path} is invalid: {exc}") from exc


def render_manifest(manifest: FormationManifest) -> str:
    """Serialize *manifest* to the exact YAML written to disk.

    ``sort_keys=False`` preserves declaration order so a manifest round-trips
    byte-identically (FR01) instead of being silently reordered on every join.
    """
    payload = manifest.model_dump(mode="json")
    return str(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False))


def write_manifest_locked(manifest_path: Path, manifest: FormationManifest) -> None:
    """Write-temp-then-replace, the sequence proven in ``state/_pin_store``.

    The caller must already hold the manifest lock (see :func:`rewrite_manifest`).
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(render_manifest(manifest), encoding="utf-8")
    os.replace(tmp, manifest_path)


@contextmanager
def _exclusive(target: Path, *, timeout_seconds: float = 10.0) -> Iterator[None]:
    """Hold an exclusive advisory lock on a sibling lock file, or refuse.

    Non-blocking acquire in a bounded poll loop rather than a blocking
    ``_lock_ex``: a blocking lock has no timeout, so a crashed holder would wedge
    every future join with no message. On expiry this RAISES — NFR02 requires a
    refusal that says so rather than a write that proceeds unlocked.
    """
    lock_path = target.with_suffix(target.suffix + _LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    handle = lock_path.open("a+")
    try:
        while True:
            try:
                _lock_ex_nb(handle.fileno())
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise FormationError(
                        f"could not acquire the formation lock at {lock_path} within {timeout_seconds}s; "
                        "another member is joining — retry, or clear the stale lock file"
                    ) from None
                time.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _lock_un(handle.fileno())
    finally:
        handle.close()


@contextmanager
def rewrite_manifest(manifest_path: Path, *, timeout_seconds: float) -> Iterator[list[FormationManifest]]:
    """Locked read-modify-write. Yields a one-slot box holding the manifest.

    The caller replaces ``box[0]`` with the revised manifest; leaving it
    untouched writes nothing, which is how an idempotent re-join avoids bumping
    the revision (FR04's migration case). The manifest is re-read INSIDE the
    lock so a concurrent join cannot be lost.
    """
    with _exclusive(manifest_path, timeout_seconds=timeout_seconds):
        original = read_manifest(manifest_path)
        box = [original]
        yield box
        revised = box[0]
        if revised is original:
            return
        write_manifest_locked(manifest_path, revised)


def resolve_active(trw_dir: Path, run_path: Path | None) -> FormationContext | None:
    """Return the formation *run_path* belongs to, ``None`` when there is none.

    Two ways a run belongs: it IS the orchestrator (its own directory holds the
    manifest), or its ``run.yaml`` carries the ``formation_id`` / ``member_id``
    stamped at join. Both are checked; neither guesses. An unreadable manifest
    RAISES rather than returning ``None`` — see the module docstring.
    """
    if run_path is None:
        return None
    own = manifest_path_for_run(run_path)
    if own.is_file():
        return FormationContext(read_manifest(own), own, None, True)
    stamped = _stamped_ids(run_path)
    if stamped is None:
        return None
    formation_id, member_id = stamped
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    if not manifest_path.is_file():
        raise FormationError(
            f"run {run_path} declares formation {formation_id!r} but its manifest is missing at {manifest_path}"
        )
    return FormationContext(read_manifest(manifest_path), manifest_path, member_id, False)


def _stamped_ids(run_path: Path) -> tuple[str, str | None] | None:
    """Read ``formation_id`` / ``member_id`` off a member run's ``run.yaml``."""
    run_yaml = run_path / "meta" / "run.yaml"
    if not run_yaml.is_file():
        return None
    try:
        data = yaml.safe_load(run_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("formation_run_yaml_unreadable", run=str(run_path), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    formation_id = data.get("formation_id")
    if not isinstance(formation_id, str) or not formation_id:
        return None
    member_id = data.get("member_id")
    return formation_id, member_id if isinstance(member_id, str) and member_id else None
