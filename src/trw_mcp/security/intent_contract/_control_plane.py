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
import re
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
    "SETTINGS_PATHS",
    "SETTINGS_SOURCE_PATH",
    "TRW_CONFIG_PATH",
    "BlobReader",
    "configured_contract_path",
    "configured_glob_sidecar_path",
    "control_plane_findings",
]

BlobReader = Callable[[str], bytes | None]

DEFAULT_CONTRACT_PATH = ".trw/contracts/must-not-happen.yaml"
SETTINGS_SOURCE_PATH = "trw-mcp/src/trw_mcp/data/settings.json"
#: Where hook registrations live. The bundle source exists only in the TRW monorepo; a user project registers
#: its hooks in `.claude/settings.json`, which this list used to omit, so C9 compared two absent blobs and a
#: deleted intent-hook registration was no finding there (UF-PRD-51 / ledger UF-079).
SETTINGS_PATHS = (SETTINGS_SOURCE_PATH, ".claude/settings.json")
DEFAULT_GLOB_SIDECAR_PATH = ".trw/contracts/enrollment.globs"
#: Claude Code's timeout for a command hook that sets none (seconds).
_CLIENT_DEFAULT_TIMEOUT = 600  # codex r3 KI2: the documented command-hook default
INTENT_HOOK_FILES = ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh")

#: Shared shell libraries the intent hooks SOURCE. They register no hook of their
#: own, which is exactly why they were the blind spot: `chmod 000
#: .claude/hooks/lib-trw.sh` was a git-invisible total disarm (probe finding N6).
#: They belong to the enrollment digest even though they belong to no registration.
#:
#: `lib-intent-guard.sh` (PRD-CORE-250-FR05) joined the tuple with the extraction
#: that created it, and it matters MORE than its sibling: `lib-trw.sh` is sourced
#: only inside a detached subshell, while this one is sourced into the deciding
#: shell and defines the recognition and decision routines. It is therefore
#: inside the hooks' trusted computing base — the same trust the hook body has —
#: and a content or mode change to it must read as `stale` for the same reason a
#: change to the hook itself does.
HOOK_SUPPORT_FILES = ("lib-trw.sh", "lib-intent-guard.sh")

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
#: The only freshness-record shapes the sidecar renderer writes (a path is one token: renderable ASCII, no space).
_G0_RE = re.compile(r"g0 ([!-~]+)")
_G1_RE = re.compile(r"g1 [0-9a-f]{64} ([!-~]+)")
_SHA_LINE_RE = re.compile(r"sha256:[0-9a-f]{64}")


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
    signatures: dict[str, str] = {}
    for settings_path in SETTINGS_PATHS:
        raw = read(settings_path)
        if raw is None:
            continue
        try:
            document = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError):
            signatures[f"{settings_path}:{_UNPARSABLE}"] = _UNPARSABLE
            continue
        hooks = document.get("hooks") if isinstance(document, dict) else None
        if not isinstance(hooks, dict):
            continue
        for event, entries in hooks.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                blob = json.dumps(_without_timeouts(entry), sort_keys=True)
                for hook_file in INTENT_HOOK_FILES:
                    if hook_file in blob:
                        signatures[f"{settings_path}:{event}:{hook_file}"] = blob
    return signatures


def _without_timeouts(entry: object) -> object:
    """*entry* with each hook's ``timeout`` dropped: a timeout is judged by :func:`_timeout_findings` instead."""
    if not isinstance(entry, dict):
        return entry
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return entry
    return {
        **entry,
        "hooks": [{k: v for k, v in h.items() if k != "timeout"} if isinstance(h, dict) else h for h in hooks],
    }


def _intent_timeouts(read: BlobReader) -> dict[str, int]:
    timeouts: dict[str, int] = {}
    for settings_path in SETTINGS_PATHS:
        raw = read(settings_path)
        try:
            document = json.loads(raw.decode("utf-8", errors="strict")) if raw is not None else None
        except (UnicodeDecodeError, ValueError):
            continue  # trw-fail-silent-allow: an unparsable file is already its own registration finding
        hooks = document.get("hooks") if isinstance(document, dict) else None
        for event, entries in hooks.items() if isinstance(hooks, dict) else ():
            for entry in entries if isinstance(entries, list) else ():
                for hook in entry.get("hooks", []) if isinstance(entry, dict) else ():
                    command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
                    for hook_file in INTENT_HOOK_FILES:
                        if hook_file in command and isinstance(hook.get("timeout"), int):
                            timeouts[f"{settings_path}:{event}:{hook_file}"] = hook["timeout"]
    return timeouts


def _bundled_timeout_floor() -> dict[str, int]:
    """The packaged timeout per intent hook: below it, the client may kill the guard mid-decision."""
    from pathlib import Path

    try:
        document = json.loads((Path(__file__).resolve().parents[2] / "data" / "settings.json").read_text("utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: no floor means any lowered timeout is a finding
        return {}
    return {
        key.rsplit(":", 1)[-1]: value
        for key, value in _intent_timeouts(lambda _p: json.dumps(document).encode()).items()
    }


def _timeout_findings(read_base: BlobReader, read_candidate: BlobReader) -> list[str]:
    """Codex UF-PRD-51-r1 KI2: update-project's legacy 5000 -> bundled migration is no weakening; a cut below the
    bundled value is (a shorter timeout lets the client kill the guard before it decides)."""
    floor = _bundled_timeout_floor()
    base = _intent_timeouts(read_base)
    findings = []
    for key, value in sorted(_intent_timeouts(read_candidate).items()):
        before = base.get(key)
        # An omitted timeout is the client default; adding a short one is a cut too (codex r2 P0-2).
        limit = floor.get(key.rsplit(":", 1)[-1], before if before is not None else _CLIENT_DEFAULT_TIMEOUT)
        if value != before and value < limit:
            findings.append(f"hook timeout lowered below {limit}s: {key} ({before or 'default'} -> {value})")
    return findings


def configured_glob_sidecar_path(read: BlobReader) -> str:
    """Resolve ``security.intent.glob_sidecar_path`` from a ``.trw/config.yaml`` blob."""
    intent = _intent_config_block(read)
    value = intent.get("glob_sidecar_path") if isinstance(intent, dict) else None
    return str(value) if isinstance(value, str) and value else DEFAULT_GLOB_SIDECAR_PATH


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


def _sidecar_matches_contract(read: BlobReader, sidecar: str) -> bool:
    """True when *sidecar*'s ``p`` lines are exactly the patterns its contract renders (or it is absent)."""
    from trw_mcp.security.intent_contract._anchors import eligible_claims
    from trw_mcp.security.intent_contract._sidecar import _patterns_for
    from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract_bytes

    raw = read(sidecar)
    if raw is None:
        return True  # absent: only the latency shortcut is gone
    contract_raw = read(configured_contract_path(read))
    try:
        contract = load_contract_bytes(contract_raw) if contract_raw is not None else None
    except ContractLoadError:
        return False  # trw-fail-silent-allow: not silent, False makes a committed sidecar a finding; the renderer writes none for an unloadable contract
    expected: list[str] = []
    for claim in eligible_claims(contract) if contract is not None else ():
        for anchor in claim.anchors:
            patterns = _patterns_for(anchor)
            if patterns is None:
                return False  # the renderer writes no sidecar then either
            expected += patterns
    lines = raw.decode("utf-8", errors="replace").splitlines()
    actual = [line[2:] for line in lines if line.startswith("p ")]
    if sorted(actual) != sorted(expected):  # order-insensitive: an equivalent render is no forgery (codex r2 KI1)
        return False
    # Codex r2 P0-1: the g records are the freshness protection (the shell defers when an artifact's bytes moved);
    # every guarded artifact needs one. Their digest VALUES are not checked: a wrong one only makes the shell defer.
    from trw_mcp.security.intent_contract._sidecar import guarded_artifacts

    # Lead ruling on codex r3 KI1: only the exact shapes the renderer writes. `g0 __missing__ <path>` satisfied a
    # "has a record" test while the shell parsed a composite filename; anything malformed or extra fails closed.
    recorded: list[str] = []
    digests = 0
    for line in lines:
        if not line or line.startswith(("#", "p ")):
            continue
        g0, g1, sha = _G0_RE.fullmatch(line), _G1_RE.fullmatch(line), _SHA_LINE_RE.fullmatch(line)
        if g0:
            recorded.append(g0.group(1))
        elif g1:
            recorded.append(g1.group(1))
        elif sha:
            digests += 1
        else:
            return False
    return sorted(recorded) == sorted(guarded_artifacts(configured_contract_path(read))) and digests == 1


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

    # PRD-CORE-254 section 13 item 4: no shell-readable cache can resist a sidecar stripped under a correct digest,
    # so a COMMITTED sidecar whose pattern lines are not the ones its contract renders is a finding (UF-PRD-51).
    # Judged by content: a marker edit is no proof of re-enrollment (codex r1 KI1).
    sidecar = configured_glob_sidecar_path(read_candidate)
    if read_base(sidecar) != read_candidate(sidecar) and not _sidecar_matches_contract(read_candidate, sidecar):
        findings.append(f"fast-path sidecar does not match its contract: {sidecar}")
    findings.extend(_timeout_findings(read_base, read_candidate))

    findings.extend(
        _missing_or_altered(_settings_hook_signatures(read_base), _settings_hook_signatures(read_candidate), "hook")
    )
    findings.extend(
        _missing_or_altered(
            _pre_commit_intent_hooks(read_base), _pre_commit_intent_hooks(read_candidate), "pre-commit hook"
        )
    )
    return tuple(findings)
