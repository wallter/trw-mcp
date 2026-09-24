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
from typing import Any

import structlog
import yaml

from trw_mcp._locking import _lock_ex_nb, _lock_un
from trw_mcp.formation._manifest import FormationError, FormationManifest

logger = structlog.get_logger(__name__)

__all__ = [
    "MANIFEST_FILENAME",
    "FormationContext",
    "canonical_path",
    "manifest_path_for_run",
    "own_slot_if_caller",
    "read_json_store",
    "read_manifest",
    "register_formation",
    "resolve_active",
    "resolve_manifest_path",
    "rewrite_manifest",
    "write_json_store",
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
    #: The run's own slot. For the orchestrator it is the slot it joined as a
    #: member of its own formation, and ``None`` when it did not (PRD-FIX-149).
    member_id: str | None
    is_orchestrator: bool


def canonical_path(path: Path) -> Path:
    """Refuse broken resolution, preserving non-existent ordinary peer paths.

    THE one canonicalizer (PRD-FIX-149 review R1): ``comms/_identity.py``'s
    ``_canonical`` used to be a byte-identical second copy of this exact
    function, reached only through the ``trw_mcp.formation`` facade so the
    package-boundary test (``test_no_adapter_imports_a_private_formation_module``)
    stays honest. Strict resolution detects loops on Python 3.13+, whose
    non-strict resolver no longer raises for them. Missing ordinary paths
    retain prior semantics.
    """
    try:
        try:
            return path.resolve(strict=True)
        except FileNotFoundError:
            return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise FormationError(f"path {path} cannot be canonically resolved: {exc}") from exc


def _own_slot(manifest: FormationManifest, run_path: Path) -> str | None:
    """The member id an OWNING run holds in its own formation, if it joined one.

    An orchestrator's ``run.yaml`` is never stamped -- it owns the manifest
    instead -- so its slot is found the only way it can be: by the run path that
    joined it, compared exactly as ``mark_member_delivered`` authenticates a
    reporter. Resolving it HERE, once, is what lets the deliver gate exclude the
    caller's own slot and the self-report write it; before, both keyed on a
    ``member_id`` that was always ``None`` for an orchestrator, so its own slot
    blocked its own delivery and nothing could ever mark it delivered (T22).
    Two slots on one run name nobody rather than guessing.

    STRUCTURAL ONLY. This answers "which member recorded *run_path* as its
    own", nothing about who is actually calling right now -- a caller can pass
    an arbitrary ``run_path`` to ``trw_deliver`` and get a structural match for
    a slot it does not own. Excluding a slot from a gate, or writing a
    self-report on its behalf, must go through :func:`own_slot_if_caller`
    instead, which additionally verifies the CALLING SESSION (PRD-FIX-149
    review R1, ledger: a peer supplying ``run_path=<orchestrator run>`` got the
    orchestrator's own slot excluded and stamped delivered).
    """
    here = canonical_path(run_path)
    slots = [m.member_id for m in manifest.members if m.run_path and canonical_path(Path(m.run_path)) == here]
    return slots[0] if len(slots) == 1 else None


def own_slot_if_caller(
    context: FormationContext,
    run_path: Path,
    *,
    pinned_run: Path | None,
    session_id: str | None,
) -> str | None:
    """*context*'s structural own-slot id, but ONLY when THIS CALL is verified as that session's.

    ``_own_slot`` (via :func:`resolve_active`) answers a purely structural
    question and is safe for a read-only projection, but NOT for a gate
    exclusion or a self-report write: a caller can pass an arbitrary
    ``run_path`` to ``trw_deliver`` and borrow a structurally-matching slot to
    skip a peer's completion evidence, or mark it delivered on the peer's
    behalf (PRD-FIX-149 review R1 -- a peer supplying
    ``run_path=<orchestrator run>`` got the orchestrator's own slot excluded
    and stamped delivered). Trust the match only when BOTH hold:

    - *pinned_run* -- read by the caller from ITS OWN pin store entry before
      this call, never from the ``run_path`` argument -- canonically equals
      *run_path*, proving the calling session did not merely name a path it
      does not own; and
    - the matched member's recorded ``pin_key`` equals *session_id*, proving
      the join that created the slot belongs to the SAME session, not merely
      the same run directory (a run directory alone is guessable/enumerable).

    Returns ``None`` (never the caller's *actual* slot) on any missing input,
    so an unauthenticated caller (no pin, no session id) is always treated as
    an ordinary peer rather than trusted by default.
    """
    member_id = context.member_id
    if member_id is None or pinned_run is None or session_id is None:
        return None
    if canonical_path(pinned_run) != canonical_path(run_path):
        return None
    member = next((m for m in context.manifest.members if m.member_id == member_id), None)
    if member is None or member.pin_key != session_id:
        return None
    return member_id


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


def registered_formations(trw_dir: Path) -> dict[str, Path]:
    """Every indexed ``formation_id`` and its manifest path (FR18 discover); raises on an unreadable index."""
    return {formation_id: manifest_path_for_run(Path(run)) for formation_id, run in _read_index(trw_dir).items()}


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


_FR18_DEFAULTS: dict[str, object] = {"open_join": False, "admitted_candidate": None, "admitted_revision": None}


def render_manifest(manifest: FormationManifest) -> str:
    """Serialize *manifest* to the exact YAML written to disk.

    ``sort_keys=False`` preserves declaration order so a manifest round-trips
    byte-identically (FR01) instead of being silently reordered on every join.
    """
    payload = manifest.model_dump(mode="json")
    # PRD-CORE-274 FR18 fields are written only when set. Builds before FR18 load
    # members with extra="forbid", so an unconditional `open_join: false` would make
    # every manifest this build rewrites unreadable to them (a silent schema
    # migration of a live formation). An unset field round-trips as its default.
    if payload.get("schema_version") is None:
        # N4: same rule as the FR18 fields below. The key appears only in a
        # manifest that already had one, so a reader-only release never makes a
        # manifest that an older peer cannot load.
        payload.pop("schema_version", None)
    for member in payload.get("members", []):
        for key, default in _FR18_DEFAULTS.items():
            if member.get(key, default) == default:
                member.pop(key, None)
    return str(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, default_flow_style=False))


def write_manifest_locked(manifest_path: Path, manifest: FormationManifest) -> None:
    """Write-temp-then-replace, the sequence proven in ``state/_pin_store``.

    The caller must already hold the manifest lock (see :func:`rewrite_manifest`).
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    # PRD-CORE-274 NFR07: owner-only. The manifest records members' pin keys and run
    # paths, which other local users must not read (0644 was observed 2026-09-19).
    write_owner_only(tmp, render_manifest(manifest).encode("utf-8"))
    os.replace(tmp, manifest_path)


def read_json_store(path: Path, *, section: str, label: str) -> dict[str, Any]:
    """Read one owner-only JSON store, or refuse (ledger N12).

    The candidate registry and the worktree-membership records are the same
    artifact twice: a ``{<section>: {key: record}}`` JSON file under
    ``.trw/runtime/``, read with an unreadable-is-a-refusal rule and written
    owner-only through a temp file. They had two copies of that shape, so a fix
    to one (a permission, an encoding, an atomicity detail) reached only half
    the stores. *label* names the artifact in the refusal, because a caller can
    act on "candidate registry" and cannot act on a path alone.

    An absent file is an EMPTY store, not an error: nothing has been recorded
    yet. A present but unparseable one raises, because silently treating it as
    empty would drop live records and re-admit whatever they were guarding.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FormationError(f"{label} {path} is unreadable: {exc}") from exc
    records = raw.get(section) if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        raise FormationError(f"{label} {path} is malformed")
    return {str(key): value for key, value in records.items()}


def write_json_store(path: Path, *, section: str, records: dict[str, Any]) -> None:
    """Write-temp-then-replace one owner-only JSON store. The caller holds the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_owner_only(tmp, (json.dumps({section: records}, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def write_owner_only(path: Path, data: bytes) -> None:
    """Create or truncate *path* at mode 0600 regardless of the umask, and write all of *data*.

    A file that already existed at a wider mode is narrowed too (fchmod). The file
    object's write loops over short writes, which a bare ``os.write`` does not.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)  # the mode argument alone is filtered by the umask
    except OSError:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


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
        manifest = read_manifest(own)
        return FormationContext(manifest, own, _own_slot(manifest, run_path), True)
    stamped = stamped_ids(run_path)
    if stamped is None:
        return None
    formation_id, member_id = stamped
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    if not manifest_path.is_file():
        raise FormationError(
            f"run {run_path} declares formation {formation_id!r} but its manifest is missing at {manifest_path}"
        )
    return FormationContext(read_manifest(manifest_path), manifest_path, member_id, False)


def stamped_ids(run_path: Path) -> tuple[str, str | None] | None:
    """Read ``formation_id`` / ``member_id`` off a member run's ``run.yaml``.

    Public because a consumer must be able to compare the STAMP against the
    manifest that was resolved from it. ``resolve_active`` follows the stamp
    through the registry index and returns whatever it points at without
    checking the manifest carries that id, so a consumer that cannot read the
    stamp cannot detect the disagreement.
    """
    run_yaml = run_path / "meta" / "run.yaml"
    if not run_yaml.is_file():
        return None
    try:
        data = yaml.safe_load(run_yaml.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("formation_run_yaml_unreadable", run=str(run_path), reason=str(exc))
        return None
    if not isinstance(data, dict):
        return None
    formation_id = data.get("formation_id")
    if not isinstance(formation_id, str) or not formation_id:
        return None
    member_id = data.get("member_id")
    return formation_id, member_id if isinstance(member_id, str) and member_id else None
