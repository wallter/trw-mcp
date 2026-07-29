"""C9 locator + control-plane scope detection (PRD-SEC-013 R8/B4).

A one-line ``enabled: false``, a deleted hook registration, or a renamed
contract file is a complete, otherwise-unattributed disarm — so the control
PLANE must cost the same signature as the controls. These helpers are pure:
they take blob readers, never git, so they are testable without a repository.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from trw_mcp.security.intent_contract.paths import (
    APPROVALS_PATH,
    CHECKPOINT_PATH,
    ENROLLMENT_EVIDENCE_PATH,
    ENROLLMENT_PATH,
    LEDGER_PATH,
    PRE_COMMIT_CONFIG_PATH,
    TRW_CONFIG_PATH,
)

__all__ = [
    "DEFAULT_CONTRACT_PATH",
    "EVIDENCE_PATHS",
    "EVIDENCE_VISIBILITY_PATH",
    "EVIDENCE_VISIBILITY_RULES",
    "HOOK_SUPPORT_FILES",
    "INTENT_HOOK_FILES",
    "PRE_COMMIT_CONFIG_PATH",
    "SETTINGS_SOURCE_PATH",
    "TRW_CONFIG_PATH",
    "BlobReader",
    "configured_contract_path",
    "control_plane_findings",
]

BlobReader = Callable[[str], bytes | None]

DEFAULT_CONTRACT_PATH = ".trw/contracts/must-not-happen.yaml"
SETTINGS_SOURCE_PATH = "trw-mcp/src/trw_mcp/data/settings.json"
INTENT_HOOK_FILES = ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh")

#: Shared shell libraries the intent hooks SOURCE. They register no hook of their
#: own, which is exactly why they were the blind spot: `chmod 000
#: .claude/hooks/lib-trw.sh` was a git-invisible total disarm (probe finding N6).
#: They belong to the enrollment digest even though they belong to no registration.
HOOK_SUPPORT_FILES = ("lib-trw.sh",)

#: Evidence files whose CONTENT is control-plane: the override ledger, its
#: anchoring checkpoint, and the approval records. The checkpoint shares a trust
#: domain with the ledger, so a forged self-consistent PAIR verifies internally
#: (documented in ledger.py's threat-model note) — requiring a signature to
#: COMMIT either one is the mechanical signal that survives that fact.
EVIDENCE_PATHS = (LEDGER_PATH, CHECKPOINT_PATH, APPROVALS_PATH)

#: `.trw/.gitignore` ignores `*.jsonl`, which silently hid BOTH the override
#: ledger and the weaken-edit approval records from every git-side check — the
#: evidence C9 is supposed to protect was never visible to it (probe finding N7,
#: 2026-07-24). Two negation rules force-track them; removing a rule re-hides the
#: evidence, so a removal is itself a control-plane finding.
EVIDENCE_VISIBILITY_PATH = ".trw/.gitignore"
_TRW_PREFIX = ".trw/"
EVIDENCE_VISIBILITY_RULES = tuple(f"!{path[len(_TRW_PREFIX) :]}" for path in EVIDENCE_PATHS if path.endswith(".jsonl"))
_INTENT_PRE_COMMIT_ID_PREFIX = "intent-"

_UNPARSABLE = "<unparsable>"


def _safe_yaml(raw: bytes | None) -> object | None:
    if raw is None:
        return None
    try:
        loaded: object = YAML(typ="safe").load(io.StringIO(raw.decode("utf-8", errors="strict")))
    except (UnicodeDecodeError, YAMLError):
        return _UNPARSABLE
    return loaded


def configured_contract_path(read: BlobReader) -> str:
    """Resolve ``security.intent.contract_path`` from a ``.trw/config.yaml`` blob."""
    document = _safe_yaml(read(TRW_CONFIG_PATH))
    if not isinstance(document, dict):
        return DEFAULT_CONTRACT_PATH
    security = document.get("security")
    if not isinstance(security, dict):
        return DEFAULT_CONTRACT_PATH
    intent = security.get("intent")
    if not isinstance(intent, dict):
        return DEFAULT_CONTRACT_PATH
    value = intent.get("contract_path")
    return str(value) if isinstance(value, str) and value else DEFAULT_CONTRACT_PATH


def _intent_config_block(read: BlobReader) -> object:
    document = _safe_yaml(read(TRW_CONFIG_PATH))
    if document is None:
        return None
    if document == _UNPARSABLE:
        return _UNPARSABLE
    if not isinstance(document, dict):
        return None
    security = document.get("security")
    if not isinstance(security, dict):
        return None
    return security.get("intent")


def _settings_hook_signatures(read: BlobReader) -> dict[str, str]:
    raw = read(SETTINGS_SOURCE_PATH)
    if raw is None:
        return {}
    try:
        document = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return {_UNPARSABLE: _UNPARSABLE}
    signatures: dict[str, str] = {}
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, dict):
        return signatures
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            blob = json.dumps(entry, sort_keys=True)
            for hook_file in INTENT_HOOK_FILES:
                if hook_file in blob:
                    signatures[f"{event}:{hook_file}"] = blob
    return signatures


def _pre_commit_intent_hooks(read: BlobReader) -> dict[str, str]:
    document = _safe_yaml(read(PRE_COMMIT_CONFIG_PATH))
    if document is None:
        return {}
    if document == _UNPARSABLE:
        return {_UNPARSABLE: _UNPARSABLE}
    signatures: dict[str, str] = {}
    repos = document.get("repos") if isinstance(document, dict) else None
    if not isinstance(repos, list):
        return signatures
    for repo in repos:
        hooks = repo.get("hooks") if isinstance(repo, dict) else None
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            hook_id = hook.get("id") if isinstance(hook, dict) else None
            if isinstance(hook_id, str) and hook_id.startswith(_INTENT_PRE_COMMIT_ID_PREFIX):
                signatures[hook_id] = json.dumps(hook, sort_keys=True, default=str)
    return signatures


def _evidence_visibility(read: BlobReader) -> frozenset[str]:
    """Which force-track rules a `.trw/.gitignore` snapshot still carries.

    Whole-line matching, never substring: an inline prose mention of a rule must
    not read as the rule being in force (repo Marker/Sentinel convention).
    """
    raw = read(EVIDENCE_VISIBILITY_PATH)
    if raw is None:
        return frozenset()
    lines = {line.strip() for line in raw.decode("utf-8", errors="replace").splitlines()}
    return frozenset(rule for rule in EVIDENCE_VISIBILITY_RULES if rule in lines)


def _missing_or_altered(base: dict[str, str], candidate: dict[str, str], label: str) -> list[str]:
    return [
        f"{label} registration removed or altered: {key}"
        for key, value in sorted(base.items())
        if candidate.get(key) != value
    ]


def control_plane_findings(
    read_base: BlobReader,
    read_candidate: BlobReader,
    *,
    removed_or_renamed: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Every C9 finding between two snapshots. Empty tuple means no disarm."""
    findings: list[str] = []

    base_locator = configured_contract_path(read_base)
    candidate_locator = configured_contract_path(read_candidate)
    if base_locator != candidate_locator:
        findings.append(f"contract locator changed: {base_locator} -> {candidate_locator}")

    # ENROLLMENT_EVIDENCE_PATH joins the marker and the contract here because it
    # is now a load-bearing enrollment signal: with it gone AND the marker gone,
    # a project that cannot ask git goes inert. A COMMITTED removal must
    # therefore cost the same signature as removing the contract itself.
    for tracked in sorted({base_locator, candidate_locator, ENROLLMENT_PATH, ENROLLMENT_EVIDENCE_PATH}):
        if tracked in removed_or_renamed:
            findings.append(f"protected file renamed, moved, or deleted: {tracked}")
        elif read_base(tracked) is not None and read_candidate(tracked) is None:
            findings.append(f"protected file removed: {tracked}")

    findings.extend(
        f"override-evidence file modified: {evidence}"
        for evidence in EVIDENCE_PATHS
        if read_base(evidence) != read_candidate(evidence)
    )

    findings.extend(
        f"override-evidence visibility rule removed from {EVIDENCE_VISIBILITY_PATH}: {rule}"
        for rule in sorted(_evidence_visibility(read_base) - _evidence_visibility(read_candidate))
    )

    base_intent = _intent_config_block(read_base)
    candidate_intent = _intent_config_block(read_candidate)
    if base_intent != candidate_intent:
        findings.append("security.intent.* control-plane config modified")

    findings.extend(
        _missing_or_altered(_settings_hook_signatures(read_base), _settings_hook_signatures(read_candidate), "hook")
    )
    findings.extend(
        _missing_or_altered(
            _pre_commit_intent_hooks(read_base), _pre_commit_intent_hooks(read_candidate), "pre-commit hook"
        )
    )
    return tuple(findings)
