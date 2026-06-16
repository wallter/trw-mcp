"""PRD wiring gate — seam registry parsing, FR-field extraction, gate check.

Belongs to the ``prd_quality.py`` validation surface. Implements PRD-CORE-190
(delivered=wired): the AARE-F §C7 wiring gate that flags public-surface FRs
which declare no consumer / wiring_test and have no covering seam entry.

Three concerns, kept cohesive in one sibling so neither ``prd_quality.py`` nor
``_prd_scoring_parsing.py`` is pushed past the 350-effective-LOC gate:

- FR01 — ``parse_seam_entries()``: coerce frontmatter ``seams:`` into typed
  ``list[SeamEntry]``; invalid entries warn-not-crash.
- FR02 — ``_extract_fr_wiring_fields()`` + ``_classify_fr_surface()``: read
  ``consumer:`` / ``wiring_test:`` / ``surface:`` lines out of an FR block and
  decide whether the FR is a public surface.
- FR03 — ``check_wiring_gate()``: per-PRD gate producing advisory warnings
  (warn mode) or failures (block mode).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from trw_mcp.models.requirements import SeamEntry, ValidationFailure
from trw_mcp.state.validation._prd_scoring_fr import _extract_fr_sections
from trw_mcp.state.validation._prd_scoring_traceability import _extract_fr_id

# ``consumer:`` / ``wiring_test:`` / ``surface:`` field lines inside an FR block.
# Case-INSENSITIVE key (audit P2-2: ``Consumer:`` / ``Surface:`` previously
# slipped through silently), leading whitespace tolerated, value = remainder
# stripped. ``(?i:...)`` scopes the case-insensitivity to the KEY group only so
# the captured VALUE retains its original case (paths/symbols are case-sensitive).
_WIRING_FIELD_RE = re.compile(
    r"^[ \t]*(?i:(consumer|wiring_test|surface))[ \t]*:[ \t]*(.*?)[ \t]*$",
    re.MULTILINE,
)

# Must-Have priority marker inside an FR block: either a ``**Priority**: Must Have``
# bolded marker or a ``priority: must-have`` field line.
_MUST_HAVE_RE = re.compile(
    r"(?i)(?:\*\*priority\*\*\s*:\s*must[\s-]?have"
    r"|^[ \t]*priority[ \t]*:[ \t]*must[\s-]?have)",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# FR01 — seam registry parsing
# ---------------------------------------------------------------------------


def parse_seam_entries(
    frontmatter: dict[str, object],
    today: date | None = None,
) -> tuple[list[SeamEntry], list[str]]:
    """Coerce the frontmatter ``seams:`` value into typed ``SeamEntry`` objects.

    Returns ``(valid_seams, warnings)``. Invalid entries do NOT crash
    validation: each entry is validated independently and a malformed entry is
    skipped with a ``seam_schema_warning`` appended to ``warnings`` (entry index
    + the validation error). A missing or empty ``seams:`` key yields ``([], [])``
    — the common, backward-compatible case (PRD-CORE-190 §5.2).

    Expiry exclusion (PRD-CORE-190 audit P1-1): a schema-valid seam whose
    ``expiry_date`` is in the past is EXCLUDED from ``valid_seams`` (so it no
    longer suppresses wiring warnings) and a ``seam_schema_warning`` recording
    the overdue days is appended. Boundary semantics match
    ``scripts/check-seam-expiry.py`` EXACTLY: a seam is expired iff
    ``expiry_date < today`` — an ``expiry_date`` equal to ``today`` is still
    valid (current through end-of-day). ``today`` defaults to ``date.today()``
    and is injectable so the gate is deterministic under test.
    """
    raw = frontmatter.get("seams")
    if not raw or not isinstance(raw, list):
        return [], []

    # trw:intentional seam expiry is a local-calendar (date-only) boundary that
    # mirrors scripts/check-seam-expiry.py exactly; a tz-aware datetime would
    # diverge the two parsers. noqa: DTZ011 is deliberate, not an oversight.
    today = today or date.today()  # noqa: DTZ011
    valid: list[SeamEntry] = []
    warnings: list[str] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"seam_schema_warning: entry {idx} is not a mapping")
            continue
        # The YAML safe loader parses an unquoted ISO date (expiry_date:
        # 2099-12-31) into a datetime.date. SeamEntry stores expiry_date as a
        # validated str for byte-identical round-trip, so normalize date -> str
        # before validation.
        normalized = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in entry.items()}
        try:
            seam = SeamEntry.model_validate(normalized)
        except Exception as exc:  # pydantic.ValidationError or coercion error
            warnings.append(f"seam_schema_warning: entry {idx} invalid — {exc}")
            continue
        # Schema-valid; now apply the temporal gate. expiry_date is a
        # validator-guaranteed ISO date string, so fromisoformat cannot raise.
        expiry = date.fromisoformat(seam.expiry_date)
        if expiry < today:
            overdue = (today - expiry).days
            warnings.append(
                f"seam_schema_warning: entry {idx} expired {overdue}d ago "
                f"(expiry_date {seam.expiry_date}) — does not cover the wiring gate"
            )
            continue
        valid.append(seam)
    return valid, warnings


# ---------------------------------------------------------------------------
# FR02 — FR-block wiring field extraction + surface classification
# ---------------------------------------------------------------------------


def _extract_fr_wiring_fields(fr_block: str) -> dict[str, list[str]]:
    """Parse ``consumer:`` / ``wiring_test:`` / ``surface:`` lines from an FR block.

    Returns a dict keyed by field name (``consumer``, ``wiring_test``,
    ``surface``) mapping to the list of declared values. ``consumer`` values
    may be comma-separated on one line; they are split and stripped. A line
    whose key is none of the three is ignored.
    """
    fields: dict[str, list[str]] = {}
    for match in _WIRING_FIELD_RE.finditer(fr_block):
        # Normalize the key to lowercase so a case-insensitive match (audit
        # P2-2: ``Consumer:``) still buckets under the canonical lowercase key.
        key = match.group(1).lower()
        value = match.group(2).strip()
        if not value:
            continue
        if key == "consumer":
            parts = [p.strip() for p in value.split(",") if p.strip()]
        else:
            parts = [value]
        fields.setdefault(key, []).extend(parts)
    return fields


def _classify_fr_surface(fr_block: str, ip_tier: str) -> bool:
    """Return True iff the FR block is a public surface (PRD-CORE-190 FR03 rule).

    Evaluated in order:
      1. An explicit ``surface: public`` line → public (authoritative, overrides
         inference). ``surface: internal`` → explicitly NOT public (exempt).
      2. (Inference fallback, only when no ``surface:`` line is present) BOTH:
         the FR is Must-Have AND the PRD's ``ip_tier`` is ``public``.
    Otherwise the FR is internal and exempt from the gate.
    """
    fields = _extract_fr_wiring_fields(fr_block)
    surface_decls = [s.strip().lower() for s in fields.get("surface", [])]
    if surface_decls:
        # Explicit annotation is authoritative. ``public`` wins; any other
        # value (notably ``internal``) means not-a-public-surface.
        return "public" in surface_decls

    # Inference fallback: Must-Have AND ip_tier public.
    is_must_have = _MUST_HAVE_RE.search(fr_block) is not None
    return is_must_have and ip_tier.strip().lower() == "public"


def _fr_is_wired(fr_block: str) -> bool:
    """Return True when the FR block declares a consumer or wiring_test field."""
    fields = _extract_fr_wiring_fields(fr_block)
    return bool(fields.get("consumer") or fields.get("wiring_test"))


def _wiring_test_path(value: str) -> str:
    """Strip a ``::nodeid`` suffix and surrounding quotes from a wiring_test value.

    ``wiring_test:`` values are commonly written as a pytest nodeid
    (``tests/test_x.py::test_fr01_wired``). Only the file portion is a
    filesystem path; the ``::test_*`` selector is not. Returns the bare path
    token (e.g. ``tests/test_x.py``).
    """
    candidate = value.strip().strip("\"'")
    if "::" in candidate:
        candidate = candidate.split("::", 1)[0]
    return candidate.strip()


def _wiring_test_fn_name(value: str) -> str:
    """Extract the bare function name from a ``::nodeid`` suffix, or '' if absent.

    ``tests/test_x.py::test_fr01_wired`` → ``test_fr01_wired``.
    ``tests/test_x.py`` (no ``::`` separator) → ``""``.
    Class-scoped nodeids (``::TestClass::test_method``) return the leaf name.
    Strips surrounding quotes and whitespace from the raw wiring_test value.
    """
    candidate = value.strip().strip("\"'")
    if "::" not in candidate:
        return ""
    # Take everything after the first ``::`` (the file-path separator).
    nodeid_part = candidate.split("::", 1)[1].strip()
    # Class-scoped: ``TestClass::test_method`` → take the leaf.
    if "::" in nodeid_part:
        nodeid_part = nodeid_part.rsplit("::", 1)[-1].strip()
    return nodeid_part


def _fn_present_in_file(file_path: Path, fn_name: str) -> bool:
    """True when ``def <fn_name>`` appears as a function definition line in ``file_path``.

    Uses a static (grep-level) text scan — no AST, no subprocess. The check is
    conservative: any line containing ``def <fn_name>(`` or ``def <fn_name> (``
    (with optional leading whitespace to cover methods) is accepted. Fails open
    on read errors (returns True so a genuine read failure is not mis-reported
    as a missing function).
    """
    if not fn_name:
        return True  # no nodeid declared → nothing to check
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True  # fail-open: cannot read → do not emit a false advisory
    needle = f"def {fn_name}("
    needle_spaced = f"def {fn_name} ("
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith((needle, needle_spaced)):
            return True
    return False


def _wiring_test_resolves(project_root: Path, raw_value: str) -> bool:
    """True if the path named by a ``wiring_test:`` value exists under ``project_root``.

    Reuses the path-escape guard pattern from
    ``_prd_integrity_paths._path_exists_under_root``: the candidate is joined
    to ``project_root``, ``.resolve()``-d, and rejected (returns False) if it
    escapes the root via ``..`` traversal or an absolute path. A bare ``::``
    nodeid with no file portion is treated as a non-path (returns False — the
    caller decides whether to warn). Fail-closed on OSError.
    """
    rel = _wiring_test_path(raw_value)
    if not rel:
        return False
    try:
        full = (project_root / rel).resolve()
        try:
            full.relative_to(project_root.resolve())
        except ValueError:
            # Absolute path or ``..`` traversal escaping the root — never wired.
            return False
        return full.exists()
    except OSError:
        return False


def _unreachable_wiring_tests(fr_block: str, project_root: Path) -> list[str]:
    """Return advisory tokens for ``wiring_test:`` values that fail reachability.

    Two checks, both advisory (never a WIRING_GATE_FAIL failure):
    1. **File existence**: path does not exist under ``project_root`` → token
       is the bare path (``tests/missing.py``).
    2. **Function-name presence** (PRD residual B2 — "collection-grade"
       rung): file exists but the declared ``::nodeid`` function name is absent
       from the file → token is ``<path>::<fn_name>`` so the caller's warning
       message names the specific missing symbol.  A static grep-level
       ``def <fn_name>(`` scan is used (no subprocess, no AST).

    ``consumer:`` stays presence-based (v1 — no consumer reachability yet).
    """
    fields = _extract_fr_wiring_fields(fr_block)
    unreachable: list[str] = []
    for value in fields.get("wiring_test", []):
        path_token = _wiring_test_path(value)
        if not path_token:
            continue
        if not _wiring_test_resolves(project_root, value):
            unreachable.append(path_token)
            continue
        # File exists — check function name presence (B2 collection-grade rung).
        fn_name = _wiring_test_fn_name(value)
        if fn_name:
            try:
                full = (project_root / path_token).resolve()
                try:
                    full.relative_to(project_root.resolve())
                except ValueError:
                    continue  # escape-guard already handled above; skip
                if not _fn_present_in_file(full, fn_name):
                    unreachable.append(f"{path_token}::{fn_name}")
            except OSError:
                pass  # fail-open: cannot resolve → skip function check
    return unreachable


# ---------------------------------------------------------------------------
# FR03 — wiring gate
# ---------------------------------------------------------------------------


def check_wiring_gate(
    content: str,
    frontmatter: dict[str, object],
    mode: str = "warn",
    today: date | None = None,
    project_root: Path | None = None,
) -> tuple[list[str], list[ValidationFailure]]:
    """Run the wiring gate over a PRD's FRs.

    Returns ``(warnings, failures)``.

    For each FR classified as a public surface (``_classify_fr_surface``), the
    gate requires one of: (a) a ``consumer:`` field, (b) a ``wiring_test:``
    field, or (c) at least one valid (non-malformed) ``seams:`` entry in
    frontmatter. If none is present:
      - warn mode (default): emit a ``wiring_gate_warning`` string.
      - block mode: emit a ``ValidationFailure`` with code ``WIRING_GATE_FAIL``.

    Wiring-test reachability (PRD residual B2 — "existence first"): when
    ``project_root`` is supplied, each declared ``wiring_test:`` path is
    resolved against the root and existence-checked (the ``::nodeid`` selector
    is stripped first). A declared-but-missing test file emits an advisory
    ``wiring_gate_warning`` naming the path — it is NEVER treated as silently
    wired. This reachability finding is ALWAYS advisory (fail-open) regardless
    of ``mode``: a missing file is a reachability concern, not the same defect
    class as a public surface that declares nothing at all. ``consumer:`` stays
    presence-based for v1. When ``project_root`` is None the reachability check
    is skipped (the original presence-only contract).

    Seam-to-FR mapping v1: ANY valid seam entry suppresses wiring warnings for
    ALL of that PRD's unwired public-surface FRs. Per-FR keyed seam mapping is
    deferred to v2 (PRD-CORE-190 §3 FR03 — orchestrator decision); not a stub,
    a documented simplification of the suppression scope.

    An expired seam (``expiry_date < today``) is excluded from coverage and
    emits a ``seam_schema_warning`` (PRD-CORE-190 audit P1-1); ``today`` is
    injectable for deterministic tests and defaults to ``date.today()``.

    Backward compatibility (FR05): when no FR is classified as a public surface,
    the gate is a no-op and returns ``([], [])`` — identical output to the
    pre-implementation baseline.
    """
    ip_tier = str(frontmatter.get("ip_tier", "") or "")
    valid_seams, seam_warnings = parse_seam_entries(frontmatter, today=today)
    has_seam_coverage = bool(valid_seams)

    warnings: list[str] = list(seam_warnings)
    failures: list[ValidationFailure] = []

    for name, block in _extract_fr_sections(content):
        if not _classify_fr_surface(block, ip_tier):
            continue
        # Wiring-test reachability (PRD residual B2): always advisory, runs even
        # for an otherwise-wired FR. A declared wiring_test: path that does not
        # resolve under project_root is surfaced rather than counted as wired.
        # The "collection-grade" rung (B2 next step): if the file exists but
        # the declared ::nodeid function name is absent, the token is
        # ``<path>::<fn_name>`` — surfaced as a function-not-found advisory.
        if project_root is not None:
            for missing in _unreachable_wiring_tests(block, project_root):
                fr_id = _extract_fr_id(name) or name.strip()
                if "::" in missing:
                    # Function-name advisory: file exists, function name absent.
                    path_part, fn_part = missing.split("::", 1)
                    warnings.append(
                        f"wiring_gate_warning: {fr_id} declares wiring_test "
                        f"`{path_part}` but function `{fn_part}` was not found "
                        f"in that file (AARE-F §C7 B2 collection-grade — "
                        f"function-name check)."
                    )
                else:
                    warnings.append(
                        f"wiring_gate_warning: {fr_id} declares wiring_test "
                        f"`{missing}` but that path does not exist under the project "
                        f"root (AARE-F §C7 delivered=wired — reachability)."
                    )
        if _fr_is_wired(block):
            continue
        if has_seam_coverage:
            # Seam-to-FR mapping v1 (GOVERNANCE TRADEOFF, deliberate — see F2 of
            # the deliver-gate governance review lane): ANY single valid seam
            # entry suppresses the wiring warning for EVERY unwired public-surface
            # FR in this PRD, not just the FR(s) the seam actually covers. This
            # under-blocks: a PRD that declares one legitimately-deferred seam can
            # carry additional genuinely-unwired public FRs with zero warning.
            # The tradeoff is accepted for v1 — keying seams to specific FRs needs
            # a per-FR seam->FR mapping the SeamEntry schema does not yet carry.
            # v2 upgrade path: add a ``covers_frs: [FR02, ...]`` field to
            # SeamEntry and gate suppression per-FR (continue only when this FR's
            # id is in some valid seam's covers_frs). Pinned by the
            # test_wiring_gate_one_seam_does_not_cover_other_unwired_frs boundary
            # test, which asserts this v1 under-block is intentional, not a bug.
            continue
        fr_id = _extract_fr_id(name) or name.strip()
        msg = (
            f"wiring_gate_warning: {fr_id} is a public surface with no "
            f"consumer:/wiring_test: field and no covering seams: entry "
            f"(AARE-F §C7 delivered=wired)."
        )
        if mode == "block":
            failures.append(
                ValidationFailure(
                    field=fr_id,
                    rule="WIRING_GATE_FAIL",
                    message=msg,
                    severity="error",
                )
            )
        else:
            warnings.append(msg)

    return warnings, failures


def extract_wiring_warnings(v2_result: object) -> list[str]:
    """Collect the complete wiring-dimension suggestion messages.

    The tool-facing ``improvement_suggestions`` list is truncated to 5
    entries, so ``trw_prd_validate`` surfaces the full wiring set under
    its own ``wiring_gate_warnings`` key (PRD-CORE-190 FR03).
    """
    suggestions = getattr(v2_result, "improvement_suggestions", [])
    return [s.message for s in suggestions if s.dimension == "wiring"]
