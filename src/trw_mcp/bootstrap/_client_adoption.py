"""Adopt a client into the install record only on proof TRW itself installed it.

``target_platforms`` (``.trw/config.yaml``) is the one record update, the uninstall planner and the
manifest ``owners`` field all read. Detection can write a client's artifacts (``shutil.which("cursor")``)
without the record ever saying so, and a client absent from the record is never refreshed or retired.

The proof covers EVERY file TRW names under the client's surface (the current render, from
``MANAGED_CLIENT_ARTIFACT_SOURCES``): each must exist and be byte-equal to that render OR to the hash
the manifest recorded for it. A file that differs, is missing or cannot be read blocks adoption and is
reported. The render alone is what proves an orphaned install whose manifest never recorded a hash.
Directory presence is never proof: a ``.cursor/`` you made yourself has no TRW-named file in it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from trw_memory._tree_removal import remove_tree

if TYPE_CHECKING:
    from ._client_integrations import ClientIntegration

logger = structlog.get_logger(__name__)


def _client_proof(
    target_dir: Path,
    client: str,
    content_hashes: Mapping[str, str],
) -> tuple[bool, str | None]:
    """``(any TRW-named file present, first blocker)``; adopt only when a file is present and none blocks."""
    from ._managed_client_artifacts import MANAGED_CLIENT_ARTIFACT_SOURCES

    renders: dict[str, bytes] = {}
    gated: set[str] = set()
    for source in MANAGED_CLIENT_ARTIFACT_SOURCES:
        if source.client != client:
            continue
        try:
            renders.update(source.contents())
            if source.licence_gated is not None:
                gated |= source.licence_gated()
        except Exception as exc:  # justified: an unavailable render is no proof; report it and adopt nothing
            return True, f"bundled render unavailable ({type(exc).__name__})"
    from ._client_ownership import owners_for_content_hashes
    from ._utils import SUPPORTED_IDES

    # A path several clients declare (the cursor hook scripts) is not this client's alone to have written,
    # so its absence proves nothing; when present it must still match.
    shared = {
        key
        for key, owners in owners_for_content_hashes(dict.fromkeys(renders, ""), SUPPORTED_IDES).items()
        if len(owners) > 1
    }
    # A licence-gated file (the distill explorer) is written only when entitled, so an unentitled
    # install legitimately lacks it. Required files never get this allowance, and a gated file that
    # IS present must still match below.
    from ._distill_entitlement import distill_artifacts_entitled

    optional = gated and not distill_artifacts_entitled(artifact=f"{client}-adoption-proof", repo_root=target_dir)
    present, blocker = False, None
    for key, rendered in sorted(renders.items()):
        try:
            on_disk = hashlib.sha256((target_dir / key).read_bytes()).hexdigest()
        except FileNotFoundError:
            if key not in shared and not (optional and key in gated):
                blocker = blocker or f"{key} is missing"
            continue
        except OSError as exc:
            return True, f"{key} is unreadable ({type(exc).__name__})"
        present = True
        if on_disk not in (hashlib.sha256(rendered).hexdigest(), content_hashes.get(key)):
            return True, f"{key} differs from the bundled render and any recorded hash"
    return present, blocker


def _merged_by_client(rel: str, client: str) -> bool:
    """True when the catalog declares *rel* a managed block or merged config of *client*: its writer merges."""
    from trw_mcp.client_profiles.catalog import client_surfaces

    return any(
        (surface.managed_block or surface.merged_config)
        and (rel == surface.relpath or rel.startswith(f"{surface.relpath}/"))
        for surface in client_surfaces(client)
    )


#: Result keys that report advice or a retirement, never an overwrite: cursor's tool-ceiling and tmux notes
#: (``info``) and a retired file's ``removed``. ``retired`` is already among the keys update merges
#: (``_ide_targets._SUB_RESULT_KEYS``, added to the recognised set at use).
_ADVISORY_KEYS: frozenset[str] = frozenset({"info", "removed"})


class WritersNotEnumerable(Exception):
    """The client's update writers could not be run to completion, so what they overwrite is unknown."""


def _run_writers(integration: ClientIntegration, scratch: Path, client: str, hashes: Mapping[str, str]) -> None:
    """Run *integration*'s updater as the real update does; anything short of a clean run raises.

    The result is seeded from ``_init_result_dict`` (the dict the real update hands its writers), so a
    writer indexing ``result["created"]`` behaves as it does in production. An error or warning any
    stage records means a stage may have stopped early (the opencode updater returns after one caught
    failure), and a key the real update does not seed is a writer output this check does not understand.
    """
    from ._update_project import _init_result_dict

    result = _init_result_dict(False)
    from ._ide_targets import _SUB_RESULT_KEYS

    seeded = set(result) | set(_SUB_RESULT_KEYS)
    integration.update(scratch, result, client, dict(hashes))
    problems = [*result.get("errors", []), *result.get("warnings", [])]
    if problems:
        raise WritersNotEnumerable(f"a {client} writer recorded: {problems[0]}")
    unknown = sorted(k for k, v in result.items() if k not in seeded | _ADVISORY_KEYS and v)
    if unknown:
        raise WritersNotEnumerable(f"a {client} writer set result keys this check does not recognise: {unknown}")


#: Environment roots a writer resolves a user-global path from (``Path.home()`` reads ``HOME``).
_HOME_ENV: tuple[str, ...] = (
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
)


@contextmanager
def _probe_home() -> Iterator[Path]:
    """Point every global-config root at a throwaway directory, so the probe writes nothing real.

    Antigravity's MCP config lives under ``~/.gemini``; a writer run for the probe would otherwise merge
    into the developer's actual file. The throwaway home starts empty and is deleted afterwards.
    """
    saved = {k: os.environ.get(k) for k in _HOME_ENV}
    with tempfile.TemporaryDirectory(prefix="trw-probe-home-") as tmp:
        home = Path(tmp)
        os.environ.update(
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local" / "share"),
            XDG_STATE_HOME=str(home / ".local" / "state"),
            XDG_CACHE_HOME=str(home / ".cache"),
        )
        try:
            yield home
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def writer_overwrites(target_dir: Path, client: str, content_hashes: Mapping[str, str]) -> list[str]:
    """Existing files *client*'s update writers would rewrite, found by running those writers.

    The writers run against a scratch copy of the transaction surface and the before/after diff names
    what they change, so the proof set is the writers' own target set and cannot drift from it (a
    hand-kept list missed ``.cursor/rules/trw-ceremony.mdc``). Merge targets are excluded (their writers
    keep user content) and so are deletions (retirement re-proves ownership at the act).

    Fails closed: raises :class:`WritersNotEnumerable`, naming why, unless every writer ran cleanly.
    """
    from trw_mcp.state._project_root_binding import installing_into

    from ._client_integrations import CLIENT_INTEGRATIONS
    from ._update_transaction import _diff_transaction_paths, _snapshot_transaction_paths, run_in_scratch

    integration = next((i for i in CLIENT_INTEGRATIONS if client in i.platform_ids), None)
    if integration is None:
        return []
    found: list[str] = []

    def apply(scratch: Path) -> None:
        before = _snapshot_transaction_paths(scratch)
        try:
            with installing_into(scratch), _probe_home():
                _run_writers(integration, scratch, client, content_hashes)
            changes = _diff_transaction_paths(before, scratch)
        finally:
            remove_tree(before, purpose="adoption probe snapshot")
        found.extend(rel for rel, kind in changes.items() if kind == "updated" and not _merged_by_client(rel, client))

    scratch_result: dict[str, list[str]] = {"errors": []}
    try:
        run_in_scratch(target_dir, scratch_result, apply)
    except WritersNotEnumerable:
        raise
    except Exception as exc:
        logger.warning("writer_targets_unavailable", client=client, exc_info=True)
        raise WritersNotEnumerable(f"the {client} writers raised {type(exc).__name__}: {exc}") from exc
    if scratch_result["errors"]:
        raise WritersNotEnumerable(f"the scratch copy failed: {scratch_result['errors'][0]}")
    return sorted(found)


def _unproven_writer_target(target_dir: Path, client: str, content_hashes: Mapping[str, str]) -> str | None:
    """The first file *client*'s writers would overwrite that is neither render-equal nor hash-equal."""
    from ._managed_client_artifacts import _cursor_rules_mdc_candidates, bundled_content_for

    try:
        targets = writer_overwrites(target_dir, client, content_hashes)
    except WritersNotEnumerable as exc:
        return f"the update writers could not be enumerated ({exc}), so nothing they overwrite is proven"
    for rel in targets:
        accepted = {content_hashes.get(rel)}
        rendered = bundled_content_for(rel)
        if rendered is not None:
            accepted.add(hashlib.sha256(rendered).hexdigest())
        if rel == ".cursor/rules/trw-ceremony.mdc":
            accepted.update(hashlib.sha256(b).hexdigest() for b in _cursor_rules_mdc_candidates())
        try:
            on_disk = hashlib.sha256((target_dir / rel).read_bytes()).hexdigest()
        except OSError:
            return f"{rel} is unreadable and update would overwrite it"
        if on_disk not in accepted:
            return f"{rel} differs from the bundled render and any recorded hash, and update would overwrite it"
    return None


def hash_proven_clients(
    target_dir: Path,
    content_hashes: Mapping[str, str] | None,
    candidates: Iterable[str],
    blockers: dict[str, str] | None = None,
) -> list[str]:
    """The *candidates* every TRW-named file of which is render-equal or hash-equal, in candidate order.

    A client that has some TRW-named file on disk but fails the proof gets its first blocker written
    into *blockers*; a client with none is silent (it was never installed here).
    ``claude-code`` is never adopted here: its ``.claude/`` is core scaffolding for every client.
    """
    from ._utils import SUPPORTED_IDES

    hashes = content_hashes or {}
    proven: list[str] = []
    for client in dict.fromkeys(candidates):
        if client == "claude-code" or client not in SUPPORTED_IDES:
            continue
        present, blocker = _client_proof(target_dir, client, hashes)
        if present and blocker is None:
            blocker = _unproven_writer_target(target_dir, client, hashes)
        if present and blocker is None:
            proven.append(client)
        elif present and blockers is not None and blocker is not None:
            blockers[client] = blocker
    return proven


def adopt_hash_proven_clients(
    target_dir: Path,
    content_hashes: Mapping[str, str] | None,
    candidates: Iterable[str],
    result: dict[str, list[str]],
) -> list[str]:
    """Append the hash-proven *candidates* to the record; return those newly recorded.

    Goes through ``_update_config_target_platforms`` so bare and explicit installs share one recorder.
    Already-recorded clients are skipped, and a client the user removed is not a candidate: its files
    went with it (uninstall) so nothing matches a hash, and the record is only ever appended to here.
    """
    from ._ide_targets_finalize import _update_config_target_platforms
    from ._template_claude_md import _recorded_targets

    recorded = set(_recorded_targets(target_dir))
    blockers: dict[str, str] = {}
    proven = hash_proven_clients(target_dir, content_hashes, [c for c in candidates if c not in recorded], blockers)
    adopted = [c for c in proven if c not in recorded]
    for client, blocker in blockers.items():
        logger.info("client_not_adopted", client=client, blocker=blocker)
        result.setdefault("warnings", []).append(f"{client} not adopted into the install record: {blocker}")
    if adopted:
        _update_config_target_platforms(target_dir, adopted, result)
        logger.info("clients_adopted_by_hash_proof", clients=adopted)
    return adopted


def adopt_for_update(
    target_dir: Path,
    ide: str | None,
    manifest_hashes: Mapping[str, str] | None,
    result: dict[str, list[str]],
) -> list[str]:
    """Adoption for a bare ``update-project`` over a project that already has a record.

    Not for an ``--ide`` run (the caller named the clients) nor a pre-record project (detection answers).
    Runs BEFORE any writer so *manifest_hashes* are compared with the bytes the last run left.
    """
    from ._template_claude_md import _recorded_targets
    from ._utils import SUPPORTED_IDES

    if ide is not None or not _recorded_targets(target_dir):
        return []
    return adopt_hash_proven_clients(target_dir, manifest_hashes, SUPPORTED_IDES, result)


def rerecord(target_dir: Path, clients: list[str], result: dict[str, list[str]]) -> None:
    """Re-append *clients* after the uncommitted-changes guard restored ``config.yaml`` whole."""
    if clients:
        from ._ide_targets_finalize import _update_config_target_platforms

        _update_config_target_platforms(target_dir, clients, result)
