"""Content-bound VerificationReceipt writer (experimental software-factory slice 1).

One public function, :func:`record_verification_receipt`: a receiver reports the
actual exit code of an intended-use check against a named commit, and the server
binds that outcome to the current bytes of the evidence files, stamps the clean
Git HEAD it observed, persists through the CORE-205 receipt store and runs the
existing verification validator over what it wrote.

Trust boundary: the caller supplies only the subject commit, a check label, the
exit code and evidence paths. ``outcome`` is derived from the exit code, the
content binding and artifact digest are read by the server, and ``git_sha`` is
observed (None on a dirty tree), never accepted. Identical inputs mint distinct
receipts (fresh 128-bit ids, no dedupe): each call is a separate observation.
Every refusal raises :class:`VerificationReceiptRefusedError` before any write.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models._evidence_core import (
    ContentBinding,
    EntryState,
    EvidenceLimits,
    RunOwnedScope,
    compute_scope_digest,
    domain_digest,
)
from trw_mcp.models._evidence_plans import VerificationOutcome
from trw_mcp.models._evidence_records import VerificationReceipt
from trw_mcp.state._evidence_identity import ProjectIdentityError, resolve_project_identity
from trw_mcp.tools._evidence_binding import build_content_binding
from trw_mcp.tools._evidence_gates import validate_verification_receipt
from trw_mcp.tools._evidence_git import clean_git_sha
from trw_mcp.tools._evidence_persistence import generate_receipt_id, write_receipt

_SHA = re.compile(r"[0-9a-f]{40}")
_METHOD = "receiver_intended_use_check"
_ORIGIN = "receiver_recorded"
_POLICY = "factory-slice-1"


class VerificationReceiptRefusedError(Exception):
    """A verification receipt was refused before any write; ``reason_code`` is stable."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class VerificationReceiptResult:
    """What was written and how the existing validator judged it."""

    receipt_id: str
    typed_receipt_state: str
    path: Path
    validation: dict[str, object]
    passed: bool
    git_sha: str | None


def _bounded_text(value: object, reason: str, *, required: bool) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise VerificationReceiptRefusedError(reason, "must be a non-empty string" if required else "must be a string")
    if len(value.encode("utf-8")) > EvidenceLimits.MAX_FREE_TEXT_BYTES or "\x00" in value:
        raise VerificationReceiptRefusedError(reason, "exceeds the free-text bound or contains NUL")
    return value


def _relative_evidence_path(project_root: Path, raw: object) -> str:
    """Return a project-relative regular-file path, or refuse; symlinks and escapes never pass."""
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"malformed path {raw!r}")
    root = project_root.resolve(strict=True)
    if ".." in raw.split("/"):
        # Normalizing a traversal segment can retarget the path (``x/../f`` is ``f``
        # even when ``x`` does not exist), so it is refused whatever it resolves to.
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"{raw!r} contains '..'")
    candidate = Path(raw) if os.path.isabs(raw) else root / raw
    relative: Path | None = None
    for prefix in (root, project_root.absolute()):
        if candidate.is_relative_to(prefix):
            relative = candidate.relative_to(prefix)
            break
    if relative is None:
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"{raw!r} is outside the project")
    if not relative.parts:
        raise VerificationReceiptRefusedError("evidence_path_invalid", "the project root is not a file")
    target = root / relative
    if target.is_symlink() or not target.is_file():
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"{raw!r} is not an existing regular file")
    try:
        resolved = target.resolve(strict=True)
    except OSError as exc:  # the file vanished between the is_file() check and here
        raise VerificationReceiptRefusedError(
            "evidence_path_invalid", f"{raw!r} is not an existing regular file"
        ) from exc
    if resolved != target:
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"{raw!r} resolves through a symlink")
    return relative.as_posix()


def _resolve_scope(project_root: Path, evidence_paths: list[str], scope_id: str) -> tuple[RunOwnedScope, str]:
    if not isinstance(evidence_paths, (list, tuple)) or not evidence_paths:
        raise VerificationReceiptRefusedError("evidence_missing", "at least one evidence path is required")
    if len(evidence_paths) > EvidenceLimits.MAX_EVIDENCE_ARTIFACTS:
        raise VerificationReceiptRefusedError("evidence_too_many", f"more than {EvidenceLimits.MAX_EVIDENCE_ARTIFACTS}")
    relatives = [_relative_evidence_path(project_root, raw) for raw in evidence_paths]
    try:
        identity = resolve_project_identity(project_root)
    except ProjectIdentityError as exc:
        raise VerificationReceiptRefusedError("project_identity_unavailable", str(exc)) from exc
    required = tuple(sorted(set(relatives)))
    scope = RunOwnedScope(
        scope_id=scope_id,
        scope_digest=compute_scope_digest(scope_id, identity, required),
        project_identity=identity,
        required_paths=required,
        provenance="operator_ownership",
    )
    return scope, relatives[0]


def _bind(scope: RunOwnedScope, project_root: Path) -> ContentBinding:
    outcome = build_content_binding(scope, project_root)
    if outcome.binding is None:
        raise VerificationReceiptRefusedError("evidence_unreadable", outcome.reason_code)
    return outcome.binding


def record_verification_receipt(
    run_path: Path,
    *,
    subject: str,
    check: str,
    exit_code: int,
    evidence_paths: list[str],
    note: str = "",
    project_root: Path | None = None,
) -> VerificationReceiptResult:
    """Write and validate one content-bound VerificationReceipt on ``run_path``.

    ``project_root`` defaults to the resolved project root; evidence paths are
    confined beneath it. Raises :class:`VerificationReceiptRefusedError` (no
    write) on any malformed input, unsafe or unreadable evidence, and (PRD-CORE-340-FR11/FR12)
    before anything else when the experimental factory gate is not enabled.
    """
    from trw_mcp.state._factory_experiment import check as factory_gate

    gate = factory_gate()
    if not gate.enabled:
        raise VerificationReceiptRefusedError(gate.reason or "factory_disabled", gate.message)
    if not isinstance(subject, str) or _SHA.fullmatch(subject) is None:
        raise VerificationReceiptRefusedError("subject_invalid", "subject must be a 40-hex lowercase commit sha")
    check_text = _bounded_text(check, "check_invalid", required=True)
    note_text = _bounded_text(note, "note_invalid", required=False)
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise VerificationReceiptRefusedError("exit_code_invalid", "exit_code must be an integer")
    run = Path(run_path)
    if not run.is_dir():
        raise VerificationReceiptRefusedError("run_invalid", f"{run} is not a run directory")
    if project_root is None:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()
    if not project_root.is_dir():
        raise VerificationReceiptRefusedError("project_root_invalid", f"{project_root} is not a directory")

    receipt_id = generate_receipt_id("verification")
    scope, artifact_rel = _resolve_scope(project_root, list(evidence_paths), f"verify-{receipt_id}")
    binding = _bind(scope, project_root)
    artifact = next(e for e in binding.entries if e.path == artifact_rel)
    if artifact.state is not EntryState.FILE or artifact.byte_digest is None:
        raise VerificationReceiptRefusedError("evidence_path_invalid", f"{artifact_rel} is not a regular file")

    mapping_digest = domain_digest("verification_mapping", {"subject": subject, "check": check_text})
    git_sha = clean_git_sha(project_root)
    receipt = VerificationReceipt(
        receipt_id=receipt_id,
        run_id=run.name,
        requirement_id=f"subject:{subject}",
        mapping_digest=mapping_digest,
        method=_METHOD,
        executor_origin=_ORIGIN,
        subject_sha=subject,
        git_sha=git_sha,
        completed_at=datetime.now(timezone.utc).isoformat(),
        content_binding=binding,
        evidence_artifact_path=artifact_rel,
        evidence_artifact_digest=artifact.byte_digest,
        outcome=VerificationOutcome.PASS if exit_code == 0 else VerificationOutcome.FAIL,
        observed_values=f"exit_code={exit_code}",
        pass_condition_evaluation=check_text,
        limitations=note_text,
        policy_mode=_POLICY,
    )
    written = write_receipt(run, "verification", receipt_id, receipt)
    if not written.ok:
        raise VerificationReceiptRefusedError("write_failed", written.reason_code)
    verdict = validate_verification_receipt(receipt, mapping_digest, project_root)
    validation: dict[str, object] = {
        "state": verdict.state.value,
        "reason_code": verdict.reason_code,
        "is_positive": verdict.is_positive,
    }
    return VerificationReceiptResult(
        receipt_id=receipt_id,
        typed_receipt_state=verdict.state.value,
        path=run / "meta" / "receipts" / "verification" / f"{receipt_id}.json",
        validation=validation,
        passed=receipt.outcome is VerificationOutcome.PASS,
        git_sha=git_sha,
    )


__all__ = ["VerificationReceiptRefusedError", "VerificationReceiptResult", "record_verification_receipt"]
