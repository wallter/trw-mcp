"""Compatibility and strict-frontmatter integrity contracts."""

from __future__ import annotations

from trw_mcp.models.requirements import ValidationFailure

# PRD-QUAL-119-FR04: every compatibility exception must carry the full removal
# contract. A seam lacking ANY field fails validation and names the gap;
# expiry is automatic (a past expiry_date fails until the seam is removed).
_COMPAT_REQUIRED_FIELDS: tuple[str, ...] = (
    "seam_id",
    "external_caller",
    "breakage_evidence",
    "owner",
    "expiry_date",
    "telemetry",
    "removal_test",
)


def _check_compatibility_exceptions(frontmatter: dict[str, object]) -> list[ValidationFailure]:
    """PRD-QUAL-119-FR04: compatibility is exception-only and time-bounded.

    ``compatibility_exceptions`` entries (legacy reader, dual write, alias, or
    fallback seams) must each name an external caller, breakage evidence, a
    migration owner, an expiry date, telemetry, and a removal test. A missing
    or blank field fails validation identifying the missing removal evidence;
    a past ``expiry_date`` fails automatically (NFR04 — expired compatibility
    never rescues a decision). Repository-internal callers get no exception:
    ``external_caller`` values starting with ``internal`` are rejected.
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    raw = frontmatter.get("compatibility_exceptions")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return [
            ValidationFailure(
                field="compatibility_exceptions",
                rule="qual119_compatibility_contract",
                message="compatibility_exceptions must be a list of seam records.",
                severity="error",
            )
        ]
    failures: list[ValidationFailure] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            failures.append(
                ValidationFailure(
                    field=f"compatibility_exceptions[{index}]",
                    rule="qual119_compatibility_contract",
                    message="seam record must be a mapping with the full removal contract.",
                    severity="error",
                )
            )
            continue
        missing = [
            field_name for field_name in _COMPAT_REQUIRED_FIELDS if not str(entry.get(field_name, "") or "").strip()
        ]
        if missing:
            failures.append(
                ValidationFailure(
                    field=f"compatibility_exceptions[{index}]",
                    rule="qual119_compatibility_contract",
                    message=(
                        f"compatibility seam is missing removal evidence: {', '.join(missing)}. "
                        "Every seam requires a named external caller, breakage evidence, owner, "
                        "expiry_date, telemetry, and a removal test."
                    ),
                    severity="error",
                )
            )
            continue
        caller = str(entry.get("external_caller", "")).strip().lower()
        if caller.startswith("internal"):
            failures.append(
                ValidationFailure(
                    field=f"compatibility_exceptions[{index}]",
                    rule="qual119_compatibility_internal_caller",
                    message="repository-internal callers receive no compatibility window; migrate the caller.",
                    severity="error",
                )
            )
            continue
        try:
            expiry = _date.fromisoformat(str(entry.get("expiry_date", "")))
        except ValueError:
            failures.append(
                ValidationFailure(
                    field=f"compatibility_exceptions[{index}]",
                    rule="qual119_compatibility_contract",
                    message="expiry_date must be an ISO date (YYYY-MM-DD).",
                    severity="error",
                )
            )
            continue
        if expiry < datetime.now(timezone.utc).date():
            failures.append(
                ValidationFailure(
                    field=f"compatibility_exceptions[{index}]",
                    rule="qual119_compatibility_expired",
                    message=(
                        f"compatibility seam {entry.get('seam_id')} expired {expiry.isoformat()}: "
                        "expiry is automatic — remove the seam and run its removal test."
                    ),
                    severity="error",
                )
            )
    return failures


def _check_frontmatter_parses(content: str) -> list[ValidationFailure]:
    """FR01 (PRD-QUAL-091): malformed frontmatter is a failure, not a silent skip.

    ``parse_frontmatter`` degrades to ``{}`` on unparseable YAML, so a PRD with a
    broken ``---`` block (duplicate keys, unclosed flow, bad alias) is
    indistinguishable from a no-frontmatter PRD and escapes every frontmatter
    gate. This re-parses strictly: if a ``---`` block exists but does NOT parse to
    a mapping, emit ``aaref_frontmatter_parse``.

    Returns ``[]`` when there is no frontmatter block at all (a distinct,
    legitimate case) or when the block parses to a mapping.
    """
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return []  # no --- block: not a malformed PRD, just frontmatter-less

    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    yaml = YAML(typ="safe")
    detail = ""
    try:
        data = yaml.load(match.group(1))
    except (YAMLError, ValueError, TypeError) as exc:
        data = None
        detail = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    else:
        if isinstance(data, dict):
            return []
        detail = f"frontmatter parsed to {type(data).__name__}, not a mapping"

    return [
        ValidationFailure(
            field="frontmatter",
            rule="aaref_frontmatter_parse",
            message=(
                "PRD begins with a `---` frontmatter delimiter but the enclosed "
                "block does not parse to a YAML mapping (likely a duplicate key, "
                "unclosed flow sequence, or undefined alias). Such a PRD silently "
                "escapes every frontmatter gate (status, functionality_level, "
                f"ip_tier). Fix the YAML so it parses. Detail: {detail}"
            ),
            severity="error",
        )
    ]
