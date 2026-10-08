"""Receiver pre-flight for a handoff record (``trw-mcp handoff check``; R2 §2 receive steps 1-6).

Belongs to the :mod:`trw_mcp.handoff` package. Before acting on a record a receiver needs to
know: is it the bytes the sender meant (digest), is it L1-valid, unexpired and still current
(no newer record of the same ``subject`` supersedes it, no fork), do the pointed files still
hold the bytes the sender read, and is the checkout where the sender left it. Record content
stays data: only ``file:`` pointers confined to the repository are opened (to hash them); every
other URI is ``not_accessed`` with a reason and never fetched or opened (R-SEC-1, R-SEC-2).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from trw_mcp.handoff._currency import handoff_files, supersession
from trw_mcp.handoff._glob import covers
from trw_mcp.handoff._jcs import JcsError, digest
from trw_mcp.handoff._repo import GitState, confined_path, raw_digest, read_capped
from trw_mcp.handoff._rules import instant
from trw_mcp.handoff._validate import AhrInputError, AhrParseError, load, validate

__all__ = ["CommitCounter", "PathLister", "check_record", "is_clean", "summary"]

JsonDoc = dict[str, Any]
#: ``commits_since(commit)``: commits on HEAD after ``commit``, or ``None`` when it is not an ancestor.
CommitCounter = Callable[[str], int | None]
#: ``paths_since(commit)``: repo-relative paths committed after ``commit``, or ``None`` when it is not an ancestor.
PathLister = Callable[[str], tuple[str, ...] | None]
_CLEAN_POINTERS = frozenset({"match", "no_digest", "not_accessed"})
_CLEAN_GIT = frozenset({"match", "not_recorded", "not_comparable"})
_FULL_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_MAX_SIDECAR_BYTES = 1 << 20
_MAX_SCOPE_LIST = 20


def _digest_section(doc: JsonDoc, expected: str | None) -> JsonDoc:
    recomputed = digest(doc)
    out: JsonDoc = {"recomputed": recomputed}
    if expected is None:
        out["status"] = "not_given"
    else:
        out |= {"expected": expected, "status": "match" if expected == recomputed else "mismatch"}
    return out


def _expiry(doc: JsonDoc, now: datetime) -> JsonDoc:
    raw = doc.get("expires_at")
    if not isinstance(raw, str):
        return {"status": "none"}
    try:
        expired = instant(raw) <= now
    except ValueError:  # trw-fail-silent-allow: reported as status "unparseable" and as a validity finding
        return {"status": "unparseable", "expires_at": raw}
    return {"status": "expired" if expired else "valid", "expires_at": raw}


def _observed(path: Path, kind: object) -> str:
    """R-INT-6: a ``record`` pointer is digested by RFC 8785; every other kind by its raw bytes."""
    if kind == "record":
        try:
            return digest(load(path))
        except (
            AhrParseError,
            AhrInputError,
            JcsError,
        ):  # trw-fail-silent-allow: not an AHR; the raw digest then drifts visibly
            pass
    return raw_digest(path)


def _pointer_check(index: int, ptr: object, root: Path) -> JsonDoc:
    """One item shaped like a read-back ``pointer_checks`` entry (schema ``$defs.readback``).

    ``reason`` (not a read-back field; ``readback-new`` drops it) says why a pointer was not accessed.
    """
    uri = ptr.get("uri") if isinstance(ptr, dict) else None
    if not isinstance(ptr, dict) or not isinstance(uri, str):
        return {"index": index, "status": "not_accessed", "reason": "no uri"}
    path, reason = confined_path(uri, root)
    if path is None:  # https/trw/other schemes, or a file: URI that escapes the repository: never opened
        return {"index": index, "status": "not_accessed", "reason": reason}
    if not path.exists():
        return {"index": index, "status": "missing"}
    if "digest" not in ptr:
        return {"index": index, "status": "no_digest"}  # nothing to compare, so nothing is hashed
    try:
        observed = _observed(path, ptr.get("kind"))
    except OSError:  # trw-fail-silent-allow: present but unreadable is reported as not_accessed, never as a match
        return {"index": index, "status": "not_accessed", "reason": "not a readable regular file"}
    status = "match" if observed == ptr["digest"] else "drift"
    return {"index": index, "status": status, "observed_digest": observed}


def _recorded_changed(ref: JsonDoc, root: Path) -> set[str] | str:
    """The sidecar's paths, after confining it and checking its digest; a status string when it cannot be read."""
    sidecar = ref.get("changed_paths")
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("uri"), str):
        return "not_checked"
    path, _ = confined_path(sidecar["uri"], root)
    expected = sidecar.get("digest")
    if path is None or not isinstance(expected, str):
        return "not_checked"
    try:
        data = read_capped(path, _MAX_SIDECAR_BYTES)
    except OSError:  # trw-fail-silent-allow: a missing, oversized or irregular sidecar cannot be compared
        return "not_checked"
    if "sha256:" + hashlib.sha256(data).hexdigest() != expected:
        return "digest_mismatch"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:  # trw-fail-silent-allow: undecodable sidecar is reported as not_checked
        return "not_checked"
    return {line for line in text.splitlines() if line}


def _changed_paths(ref: JsonDoc, now: GitState, root: Path) -> str:
    """Compare the sidecar's paths with the current ones."""
    recorded = _recorded_changed(ref, root)
    if isinstance(recorded, str):
        return recorded
    return "match" if recorded == set(now.changed) else "drift"


def _scope(doc: JsonDoc, root: Path | None, now: GitState, paths_since: PathLister) -> JsonDoc | None:
    """Paths changed since the record that ``objective.paths`` does not cover: a warning, never a finding.

    "Changed since the record" is the files committed after the recorded commit plus the current
    working-tree changes, minus the record's own baseline (its ``changed_paths`` sidecar; empty only when
    the recorded tree was clean). Without a trustworthy baseline the scope is ``not_checked`` rather than
    blaming pre-existing edits. Nothing here says WHO made a change: a shared tree has several writers.
    """
    objective = doc.get("objective")
    patterns = objective.get("paths") if isinstance(objective, dict) else None
    if not isinstance(patterns, list) or not patterns or root is None:
        return None
    as_of = doc.get("as_of")
    ref = as_of.get("base_ref") if isinstance(as_of, dict) else None
    if not isinstance(ref, dict) or ref.get("tree_state") not in ("clean", "dirty"):
        return {"status": "not_checked", "reason": "the record has no clean or dirty baseline"}
    baseline = set() if ref["tree_state"] == "clean" else _recorded_changed(ref, root)
    if isinstance(baseline, str):
        return {"status": "not_checked", "reason": f"baseline sidecar {baseline}"}
    changed = set(now.changed)
    commit = ref.get("commit")
    if isinstance(commit, str):
        committed = paths_since(commit)
        if committed is None:
            return {"status": "not_checked", "reason": "the recorded commit is not in HEAD's history"}
        changed |= set(committed)
    globs = [p for p in patterns if isinstance(p, str)]
    outside = sorted(p for p in changed - baseline if not any(covers(g, p) for g in globs))
    return {"status": "checked", "outside": outside[:_MAX_SCOPE_LIST], "outside_count": len(outside)}


def _git_section(doc: JsonDoc, root: Path | None, now: GitState, commits_since: CommitCounter) -> JsonDoc:
    """Tree-state and changed-path drift are findings; new commits on top of the recorded one are information."""
    as_of = doc.get("as_of")
    ref = as_of.get("base_ref") if isinstance(as_of, dict) else None
    if not isinstance(ref, dict):
        return {"status": "not_recorded"}
    recorded = {k: ref[k] for k in ("commit", "tree_state", "branch") if k in ref}
    out: JsonDoc = {"recorded": recorded}
    commit = ref.get("commit")
    if commit is None and ref.get("tree_state") in ("unknown", "not_applicable"):
        return out | {"status": "not_comparable"}
    if root is None:
        return out | {"status": "unavailable", "current": {"tree_state": "unknown"}}
    out["current"] = {"commit": now.commit, "tree_state": now.tree_state} | (
        {"branch": now.branch} if now.branch else {}
    )
    if isinstance(commit, str) and not _FULL_COMMIT.fullmatch(commit):
        return out | {"status": "abbreviated_commit"}  # a short hash cannot pin the base unambiguously
    status = "match" if ref.get("tree_state") == now.tree_state else "drift"
    if isinstance(commit, str):
        count = commits_since(commit)
        if count is None:
            status = "diverged"  # the recorded commit is not in HEAD's history
        else:
            out["commits_since"] = count
    if ref.get("tree_state") == "dirty" and now.tree_state == "dirty":
        out["changed_paths"] = _changed_paths(ref, now, root)
        if out["changed_paths"] in ("drift", "digest_mismatch") and status == "match":
            status = "drift"
    return out | {"status": status}


def _no_history(_commit: str) -> int | None:
    return None


def _no_paths(_commit: str) -> tuple[str, ...] | None:
    return None


def check_record(
    path: Path,
    *,
    expected_digest: str | None,
    now: datetime,
    root: Path | None,
    git: GitState,
    commits_since: CommitCounter = _no_history,
    paths_since: PathLister = _no_paths,
) -> JsonDoc:
    """Run every receiver pre-flight check on one handoff file; the report is JSON-serializable.

    ``root`` is the git top-level of the record (``None`` outside git; pointers then resolve
    against the working directory) and ``git`` the current state of that checkout with the
    record's own files excluded.
    """
    doc = load(path)
    if doc.get("type") != "handoff":
        raise AhrInputError("check takes a handoff record")
    path = path.resolve()
    base = root or Path.cwd().resolve()
    findings = validate(doc)
    pointers = doc.get("next_read")
    currency = supersession(doc, path, handoff_files(path, base), now) if not findings else {"status": "not_checked"}
    report: JsonDoc = {
        "record": str(path),
        "handoff_id": doc.get("handoff_id"),
        "digest": _digest_section(doc, expected_digest),
        "validity": {"valid": not findings, "findings": [f.as_dict() for f in findings]},
        "expiry": _expiry(doc, now),
        "supersession": currency,
        "pointer_checks": [_pointer_check(i, p, base) for i, p in enumerate(pointers)]
        if isinstance(pointers, list)
        else [],
        "git": _git_section(doc, root, git, commits_since),
    }
    scope = _scope(doc, root, git, paths_since)
    if scope is not None:
        report["scope"] = scope
    return report


def is_clean(report: JsonDoc) -> bool:
    sup = report["supersession"]
    return (
        report["digest"]["status"] != "mismatch"
        and report["validity"]["valid"]
        and report["expiry"]["status"] in ("valid", "none")
        and sup["status"] == "current"
        and not sup.get("tier_downgrade")
        and not sup.get("duplicate_id")
        and all(pc["status"] in _CLEAN_POINTERS for pc in report["pointer_checks"])
        and report["git"]["status"] in _CLEAN_GIT
    )


def _failing(report: JsonDoc) -> list[str]:
    """The report sections that keep it from being clean, in report order (dogfood 2026-10-08: a bare
    "FINDINGS" next to "valid: True (0 findings)" read as a contradiction)."""
    sup = report["supersession"]
    failing = {
        "digest": report["digest"]["status"] == "mismatch",
        "validity": not report["validity"]["valid"],
        "expiry": report["expiry"]["status"] not in ("valid", "none"),
        "supersession": sup["status"] != "current" or bool(sup.get("tier_downgrade") or sup.get("duplicate_id")),
        "pointers": any(pc["status"] not in _CLEAN_POINTERS for pc in report["pointer_checks"]),
        "git": report["git"]["status"] not in _CLEAN_GIT,
    }
    return [name for name, bad in failing.items() if bad]


def summary(report: JsonDoc) -> list[str]:
    """A few plain lines for a human; the JSON report stays the normative output."""
    counts: dict[str, int] = {}
    for pc in report["pointer_checks"]:
        counts[pc["status"]] = counts.get(pc["status"], 0) + 1
    sup = report["supersession"]
    newer = f" (newest: {sup['newest']['handoff_id']})" if "newest" in sup else ""
    newer += "".join(f" {key}: {len(sup[key])}" for key in ("tier_downgrade", "duplicate_id") if sup.get(key))
    return [
        f"handoff {report['handoff_id']}: "
        + ("clean" if is_clean(report) else f"FINDINGS in {', '.join(_failing(report))} - do not act yet"),
        f"  digest: {report['digest']['status']}   valid: {report['validity']['valid']}"
        f" ({len(report['validity']['findings'])} findings)   expiry: {report['expiry']['status']}",
        f"  supersession: {sup['status']}{newer}",
        "  pointers: " + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"),
        f"  git: {report['git']['status']}"
        + (f" ({report['git']['commits_since']} commits since)" if report["git"].get("commits_since") else ""),
    ] + (
        [
            f"  scope warning: {report['scope']['outside_count']} path(s) changed since the record outside objective.paths"
        ]
        if report.get("scope", {}).get("outside_count")
        else []
    )
