"""Factory START/READY/USED reader behind ``trw-mcp factory status`` (PRD-CORE-340-FR10).

Reads ``<run>/meta/events.jsonl`` and counts only ``checkpoint`` events whose
message is a JSON object with ``factory == 1`` (FACTORY-MVP-PLAN §2.1). Attempts
are keyed by (canonical run path, attempt). Reference resolution is identity
only: a ``referenced`` receipt says the file exists and names this receipt, never
that it passed. Receipts are resolved by file existence under
``<run>/meta/receipts/<family>/<id>.json`` with stdlib JSON.

Kinds are START, READY, USED and VOID. VOID retracts an attempt that reached READY: a receiver's FAIL before any
USED, or its own USED (attempt shows ``voided``, leaves ready_to_used). It is ordered by journal line, not by ``ts``
(validated but unused). A VOID before any READY, or on an attempt with no START, is ignored with a diagnostic; a
second VOID for the same attempt is a no-op; a USED recorded after a VOID does not resurrect it (rework is a new id).

FR09: a USED completes only when each referenced verification receipt parses, matches its id and mapping
digest (the outcome itself is unsigned) and records PASS (the RECORDED outcome, see :func:`trw_mcp.state._factory_verdict.verification_verdict`). A recorded FAIL is
``failed``; an unparsable, inconsistent or inconclusive receipt is ``unverified`` (distinct from ``unresolved``, a
missing/unreadable/foreign reference). Freshness never excludes: ``stale`` (current tree moved) and
``binding_unverifiable`` (bound files do not match their git blobs at subject_sha) are per-attempt flags with
aggregate counts. A voided attempt is counted once, as voided.

Read-only: nothing is written and no network is opened. Every entry goes through
:func:`trw_mcp.state._factory_experiment.check` first (FR11/FR12); the legacy
``scripts/factory_status.py`` and the CLI both call :func:`run_status`.

Exit codes: 0 output without diagnostics, 1 any diagnostic, 2 usage error or
disabled/config error, 3 experiment overdue.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trw_mcp.models._evidence_core import EvidenceLimits
from trw_mcp.state import _factory_read as fread
from trw_mcp.state._factory_experiment import BANNER, LIMITATIONS, check
from trw_mcp.state._factory_verdict import aggregate_counts, verification_verdict

__all__ = ["CELL_KEYS", "render_run", "report_run", "run_status"]

_KINDS = ("START", "READY", "USED")
_ALL_KINDS = (*_KINDS, "VOID")  # VOID: the receiver retracts its own USED (append-only; rework = new attempt id)
_REASON_MAX = 200
_READY_FAMILIES = ("build", "review")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CELL_KEYS = ("model_id", "tier", "effort", "client")  # the only START extras echoed (single source)
_BAD_REF = "receipt reference must match [A-Za-z0-9][A-Za-z0-9._-]*"
_CELL_VALUE = re.compile(r"^[A-Za-z0-9._:/+-]{1,64}$")


def _cell_keys(start: dict[str, Any]) -> dict[str, str]:
    """Echo the whitelisted cell keys; a present but non-conforming value becomes ``invalid``."""
    return {
        k: v if isinstance(v, str) and _CELL_VALUE.fullmatch(v) else "invalid"
        for k in CELL_KEYS
        if k in start
        for v in [start[k]]
    }


def _ts(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:  # trw-fail-silent-allow: caller emits a path:line ts diagnostic for None
        return None
    return parsed if parsed.tzinfo else None


def _schema_error(p: dict[str, Any]) -> str | None:
    if p.get("kind") not in _ALL_KINDS:
        return f"kind must be one of {_ALL_KINDS}"
    attempt = p.get("attempt")
    if not (isinstance(attempt, str) and _CELL_VALUE.fullmatch(attempt)):
        return "attempt id must match [A-Za-z0-9._:/+-]{1,64}"
    receipts = p.get("receipts")
    if p["kind"] == "VOID":
        reason = p.get("reason")
        if receipts is not None:
            return "VOID carries no receipts"
        if not (isinstance(reason, str) and 0 < len(reason) <= _REASON_MAX and reason.isprintable()):
            return f"VOID requires a printable reason of 1-{_REASON_MAX} characters"
        return None
    if p["kind"] == "START":
        return "START carries no receipts (START forbids receipts)" if receipts else None
    if not isinstance(receipts, dict) or not receipts:
        family = "build" if p["kind"] == "READY" else "verification"
        return f'{p["kind"]} requires a nonempty receipts map, e.g. "receipts": {{"{family}": ["{family}-<id>"]}}'
    for family, refs in receipts.items():
        if not (isinstance(family, str) and _SAFE.fullmatch(family)):
            return _BAD_REF
        if p["kind"] == "READY" and family not in _READY_FAMILIES:
            return "READY may reference build/review receipts; verification belongs to USED"
        if not isinstance(refs, list) or not refs:
            return 'each receipts family must be a nonempty list, e.g. "receipts": {"build": ["build-<id>"]}'
        for ref in refs:
            rid = ref.get("receipt_id") if isinstance(ref, dict) else ref
            if isinstance(ref, dict) and not isinstance(ref.get("run_path"), str):
                return "receipts entries must be an id or {run_path, receipt_id}"
            if isinstance(ref, dict) and not ref["run_path"].isprintable():
                return "receipt run_path must be printable text"  # fixed text: never echo it
            if not isinstance(rid, str):
                return "receipts entries must be an id or {run_path, receipt_id}"
            if not _SAFE.fullmatch(rid):
                return _BAD_REF
    return None


def _project_runs_dir(run: Path) -> Path | None:
    for parent in run.parents:
        if parent.name == "runs" and parent.parent.name == ".trw":
            return parent
    return None


def _resolve(run: Path, family: str, ref: str | dict[str, Any], subject: object) -> tuple[str, str]:
    """Return (label, status) for one receipt reference."""
    runs_dir = _project_runs_dir(run)
    rid = ref if isinstance(ref, str) else ref["receipt_id"]
    label = f"{family}:{rid}"
    anchor, prefix, owner_name = run, "", run.name
    if isinstance(ref, dict):
        # A cross-run read walks from the runs dir with no link followed (no resolve-then-open window).
        rel = fread.run_relative(runs_dir, runs_dir.parent.parent, ref["run_path"]) if runs_dir else None
        if runs_dir is None or rel is None:
            return label, "path_escape"
        anchor, prefix, owner_name = runs_dir, f"{rel}/", rel.rsplit("/", 1)[-1]
    # Unreachable via report_run (the schema already rejects these); kept as defense in depth.
    if not _SAFE.fullmatch(family) or not _SAFE.fullmatch(rid):
        return label, "path_escape"
    raw, state = fread.read_bounded(
        anchor, f"{prefix}meta/receipts/{family}/{rid}.json", EvidenceLimits.MAX_CANONICAL_RECEIPT_BYTES
    )
    if raw is None:
        return label, state
    try:
        body = json.loads(raw)
    except ValueError:  # trw-fail-silent-allow: an unparsable receipt is the malformed state
        return label, "malformed"
    if not isinstance(body, dict) or body.get("receipt_id") != rid:
        return label, "malformed"
    if body.get("run_id") != owner_name:
        return label, "wrong_run"
    if family == "build" and isinstance(subject, str) and subject:
        sha = body.get("git_sha")
        if not sha:
            return label, "unbound"
        if sha != subject:
            return label, "build_sha_mismatch"
    return label, "referenced"


_Seen = dict[tuple[str, str], tuple[datetime, dict[str, Any], int]]


def _classify(raw: str) -> tuple[str, Any, str]:
    """Classify one event line: ("skip"|"prose"|"diag"|"factory", payload-or-message, ts)."""
    try:
        row = json.loads(raw)
    except ValueError:
        return "diag", "event row is not valid JSON", ""
    if not isinstance(row, dict):
        return "diag", "event row is not a JSON object", ""
    msg = row.get("message")
    if row.get("event") != "checkpoint" or not isinstance(msg, str):
        return "skip", None, ""
    payload: Any = None
    if msg.lstrip().startswith("{"):
        try:
            payload = json.loads(msg)
        except ValueError:
            if '"factory"' in msg:
                return "diag", "factory-looking message is not valid JSON", ""
    if not isinstance(payload, dict) or payload.get("factory") != 1:
        return "prose", None, ""
    return "factory", payload, str(row.get("ts"))


def _load(events: Path, diags: list[str], malformed: list[int]) -> tuple[_Seen, set[str], int]:
    seen: _Seen = {}
    conflicts: set[str] = set()
    prose = 0
    for n, chunk in enumerate(fread.iter_lines(events.parent.parent, "meta/events.jsonl"), 1):
        where = f"{events}:{n}:"
        if chunk is fread.TRUNCATED:  # not a line: no line number, not counted as a malformed line
            diags.append(f"{events}: journal_truncated: journal exceeds {fread.STREAM_MAX} bytes; stopped")
            break
        if chunk is fread.LONG_LINE:
            diags.append(f"{where} oversize: line exceeds {fread.LINE_MAX} bytes")
            malformed.append(n)
            continue
        if not isinstance(chunk, bytes):  # unreachable: iter_lines yields bytes or the two sentinels above
            continue
        raw = chunk.decode("utf-8", "replace")
        if not raw.strip():
            continue
        kind, payload, ts = _classify(raw)
        if kind == "prose":
            prose += 1
        elif kind == "diag":
            diags.append(f"{where} {payload}")
            malformed.append(n)
        elif kind == "factory":
            when = _ts(ts)
            problem = _schema_error(payload) or (None if when else "ts is not a timezone-aware ISO time")
            if problem or when is None:
                diags.append(f"{where} {problem}")
                malformed.append(n)
                continue
            key = (payload["attempt"], payload["kind"])
            if key[1] == "VOID":
                if (key[0], "READY") not in seen:  # a VOID before any READY retracts nothing: never claims the slot
                    diags.append(_ignored_void(where, key[0]))
                    continue
                if key in seen:  # a second VOID for a retracted attempt is redundant, not a conflict
                    continue
            if key not in seen:
                seen[key] = (when, payload, n)
            elif seen[key][1] != payload:
                conflicts.add(key[0])
                diags.append(f"{where} conflict: {key[1]} for attempt {key[0]} differs from line {seen[key][2]}")
    return seen, conflicts, prose


def _intervals(
    attempt: str, seen: _Seen, conflicts: set[str], diags: list[str], events: Path
) -> tuple[dict[str, float], bool]:
    found: dict[str, float] = {}
    bad = attempt in conflicts
    for label, (a, b) in {"start_to_ready": ("START", "READY"), "ready_to_used": ("READY", "USED")}.items():
        if bad or (attempt, a) not in seen or (attempt, b) not in seen:
            continue
        found[label] = (seen[(attempt, b)][0] - seen[(attempt, a)][0]).total_seconds()
        if found[label] < 0:
            bad = True
            diags.append(f"{events}:{seen[(attempt, b)][2]}: {label} is negative for attempt {attempt}")
    return found, bad


def _ignored_void(where: str, attempt: str, why: str = "does not follow a READY") -> str:
    """The diagnostic every ignored VOID carries; the ``VOID for attempt <id> `` prefix is a stable contract."""
    return f"{where} VOID for attempt {attempt} {why} and is ignored"


def _interval_summary(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {"n": len(values), "median": statistics.median(values), "max": max(values)}


def report_run(run: Path, now: datetime) -> dict[str, Any]:
    """One run's structured report; ``diagnostics`` holds every ``path:line: reason``."""
    run = run.resolve()
    diags: list[str] = []
    events = run / "meta" / "events.jsonl"
    result: dict[str, Any] = {"run": str(run), "attempts": [], "diagnostics": diags, "unreadable": False}
    malformed: list[int] = []
    try:
        seen, conflicts, prose = _load(events, diags, malformed)
    except OSError as exc:
        diags.append(
            f"{events}:0: {fread.refusal_state(exc, run, 'meta/events.jsonl')}: {exc.strerror or type(exc).__name__}"
        )
        result["unreadable"] = True
        return result
    result.update(
        observed_utc=now.astimezone(timezone.utc).isoformat(),
        prose_checkpoints=prose,
        conflicting=len(conflicts),
        malformed=len(malformed),
    )
    samples: dict[str, list[float]] = {"start_to_ready": [], "ready_to_used": []}
    counts = dict.fromkeys(("completed", "incomplete", "excluded", "unresolved", "voided", "failed", "unverified"), 0)
    runs_dir = _project_runs_dir(run)
    project_root = runs_dir.parent.parent if runs_dir else None
    for attempt in sorted({a for a, _ in seen}):
        found, bad = _intervals(attempt, seen, conflicts, diags, events)
        row: dict[str, Any] = {"attempt": attempt, **found, "receipts": []}
        if (attempt, "START") in seen:  # echo only the cell keys, bounded; printed by the cost report and --json
            row["start_extra"] = _cell_keys(seen[(attempt, "START")][1])
        if (attempt, "READY") in seen and (attempt, "USED") not in seen:
            row["ready_age"] = (now - seen[(attempt, "READY")][0]).total_seconds()
        missing = [k for k in _KINDS if (attempt, k) not in seen]
        unresolved = False
        used_refs: list[str | dict[str, Any]] = []
        for kind in ("READY", "USED"):
            if (attempt, kind) not in seen:
                continue
            _, payload, line = seen[(attempt, kind)]
            for family, refs in payload["receipts"].items():
                for ref in refs:
                    label, state = _resolve(run, family, ref, payload.get("subject_sha"))
                    row["receipts"].append({"kind": kind, "label": label, "state": state})
                    if kind == "USED" and family == "verification" and state in ("referenced", "unbound"):
                        used_refs.append(ref)
                    if state not in ("referenced", "unbound"):
                        diags.append(f"{events}:{line}: receipt {label} {state}")
                        unresolved = unresolved or (kind == "USED" and family == "verification")
            if kind == "USED" and "verification" not in payload["receipts"]:
                diags.append(f"{events}:{line}: USED for attempt {attempt} has no verification receipt reference")
                unresolved = True
        has_void = (attempt, "VOID") in seen  # loaded only when it followed a READY
        # A VOID retracts an attempt that reached READY (a receiver's FAIL before any USED, or its own USED).
        # It needs a START to be an attempt at all.
        voided = has_void and (attempt, "START") in seen and (attempt, "READY") in seen
        if has_void and not voided:
            diags.append(_ignored_void(f"{events}:{seen[(attempt, 'VOID')][2]}:", attempt, "has no START"))
        # FR09: a resolved USED still completes only if each receipt is a PASS that revalidates now.
        # A VOID retracts the USED first: a voided attempt is never also failed/unverified (counted once).
        judged = (
            []
            if (missing or unresolved or has_void or bad)
            else [verification_verdict(run, r, project_root) for r in used_refs]
        )
        verdicts = [v for v, _ in judged]
        for _, flags in judged:  # descriptive freshness/binding flags never move an attempt out of completed
            row.update({k: v for k, v in flags.items() if v})
        failed, unverified = "fail" in verdicts, "unverified" in verdicts
        if unverified and not failed:
            diags.append(
                f"{events}:{seen[(attempt, 'USED')][2]}: USED verification receipt for attempt {attempt} is unverified"
            )
        row["status"] = (
            "excluded"
            if bad
            else "voided"
            if voided
            else "incomplete"
            if missing
            else "unresolved"
            if unresolved
            else "failed"
            if failed
            else "unverified"
            if unverified
            else "completed"
        )
        row["missing"] = [] if bad or voided else missing
        counts[row["status"]] += 1
        # Every non-excluded attempt contributes start_to_ready; ready_to_used needs USED evidence that resolved (FR09/FR10).
        for label, secs in found.items():
            if not bad and not (label == "ready_to_used" and (unresolved or has_void or failed or unverified)):
                samples[label].append(secs)
        result["attempts"].append(row)
    result.update(counts=counts, intervals={k: _interval_summary(v) for k, v in samples.items()})
    return result


def render_run(report: dict[str, Any]) -> list[str]:
    """The line-oriented rendering of :func:`report_run`."""
    out = [f"run {report['run']}"]
    if report["unreadable"]:
        return [*out, *report["diagnostics"], "flag INCOMPLETE"]
    out += [f"observed_utc {report['observed_utc']}", f"prose_checkpoints={report['prose_checkpoints']}"]
    for row in report["attempts"]:
        parts = [f"attempt {row['attempt']}"]
        parts += [f"{k}={row[k]:.0f}s" for k in ("start_to_ready", "ready_to_used") if k in row]
        if "ready_age" in row:
            parts.append(f"ready_age={row['ready_age']:.0f}s")
        parts += [k for k in ("stale", "evidence_invalid", "binding_unverifiable") if row.get(k)]
        parts.append(f"status={row['status']}" + (f" missing={','.join(row['missing'])}" if row["missing"] else ""))
        out.append("  " + " ".join(parts))
        out += [f"    {r['kind']} {r['label']} {r['state']}" for r in row["receipts"]]
    out.append("counts " + " ".join(f"{k}={v}" for k, v in report["counts"].items()))
    for name, summary in report["intervals"].items():
        out.append(
            f"{name} not_measured"
            if summary is None
            else f"{name} median={summary['median']:.0f}s max={summary['max']:.0f}s n={summary['n']}"
        )
    out += report["diagnostics"]
    if report["diagnostics"]:
        out.append("flag INCOMPLETE")
    return out


def _now(text: str | None) -> datetime | None:
    return datetime.now(timezone.utc) if not text else _ts(text)


def run_status(runs: list[Path], now_text: str | None, as_json: bool) -> int:
    """Gate, report every run, print, and return the process exit code."""
    when = _now(now_text)
    if when is None:
        print("factory status: --now must be a timezone-aware ISO time", file=sys.stderr)
        return 2
    gate = check()
    if not gate.enabled and gate.state != "overdue":
        document: dict[str, Any] = {"error": gate.reason, "detail": gate.message}
        print(json.dumps(document) if as_json else gate.message, file=None if as_json else sys.stderr)
        return gate.exit_code
    reports = [report_run(run, when) for run in runs]
    total = sum(len(r["diagnostics"]) for r in reports)
    seen = sum(len(r["attempts"]) for r in reports)
    denominators = {"runs": len(reports), "attempts_seen": seen}
    for label in ("start_to_ready", "ready_to_used"):
        denominators[f"{label}_n"] = sum((r.get("intervals", {}).get(label) or {}).get("n", 0) for r in reports)
    denominators.update(aggregate_counts(reports))  # NFR03: keys only added; "voided" kept in place
    if as_json:
        document = {
            "banner": BANNER,
            "limitations": list(LIMITATIONS),
            "descriptive_only": True,
            "runs": reports,
            "denominators": denominators,
        }
        if gate.state == "overdue":
            document.update(error=gate.reason, detail=gate.message)
        print(json.dumps(document))
    else:
        print(gate.message if gate.state == "overdue" else BANNER)
        print("limits: " + "; ".join(LIMITATIONS))
        print("descriptive_only: no ranking, no throughput claim, no acceptance authority")
        for report in reports:
            print("\n".join(render_run(report)))
        print("denominators " + " ".join(f"{k}={v}" for k, v in denominators.items()))
    return gate.exit_code or (1 if total else 0)
