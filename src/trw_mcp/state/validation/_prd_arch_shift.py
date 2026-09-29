"""The architectural-shift checklist rule (PRD-INFRA-199-FR05).

Review findings on an architectural change cluster by class: untrusted checkout input,
unbounded work on a shared daemon, lost updates after an executor split, a control some
path skips. Each class is cheaper to close at design time than to find one site at a time
in review. So an open PRD that touches a daemon boundary, checkout input, an executor or a
control must carry the template's checklist: one row per class, A to G.

Detection is a keyword heuristic over the PRD text. The frontmatter key
``architectural_shift`` overrides it either way: ``true`` requires the checklist whatever
the text says, ``false`` records that the heuristic misfired.
"""

from __future__ import annotations

import re

from trw_mcp.models.requirements import ValidationFailure

ARCH_SHIFT_RULE = "architectural_shift_checklist"
#: The classes the checklist names, in order (the review taxonomy's letters).
CLASSES = "ABCDEFG"
#: Statuses whose design is settled: the rule does not reopen them.
_SETTLED = frozenset({"done", "implemented", "deprecated", "superseded", "merged"})
_TRIGGER = re.compile(
    r"\bdaemon\b|\bcheckout[- ](?:input|file|store|path|supplied|data)"
    r"|\bexecutor\b|\b(?:egress|privacy|security|documented) control\b",
    re.IGNORECASE,
)
_HEADING = re.compile(r"^(#{2,4})\s+.*architectural-shift checklist.*$", re.IGNORECASE | re.MULTILINE)


def _requires_checklist(frontmatter: dict[str, object], content: str) -> bool:
    flag = frontmatter.get("architectural_shift")
    if isinstance(flag, bool):
        return flag
    if str(frontmatter.get("status", "")).strip().lower() in _SETTLED:
        return False
    return _TRIGGER.search(content) is not None


def _missing_classes(content: str) -> str | None:
    """The classes the checklist section lacks ("" when complete), or None when there is no section."""
    heading = _HEADING.search(content)
    if heading is None:
        return None
    level = len(heading.group(1))
    rest = content[heading.end() :]
    end = re.search(rf"^#{{1,{level}}}\s", rest, re.MULTILINE)
    section = rest[: end.start()] if end else rest
    rows = {m.group(1) for m in re.finditer(r"^\s*[|-]\s*\**([A-G])\**\s*[|:.]", section, re.MULTILINE)}
    return "".join(c for c in CLASSES if c not in rows)


def architectural_shift_failures(frontmatter: dict[str, object], content: str) -> list[ValidationFailure]:
    """An ``error`` finding when a PRD that needs the checklist lacks it, or lacks a class row."""
    if not _requires_checklist(frontmatter, content):
        return []
    missing = _missing_classes(content)
    if missing == "":
        return []
    what = "no 'Architectural-shift checklist' section" if missing is None else f"no row for class(es) {missing}"
    return [
        ValidationFailure(
            field="architectural_shift",
            rule=ARCH_SHIFT_RULE,
            message=(
                f"this PRD touches a daemon boundary, checkout input, an executor or a control but has {what}: "
                "add the template's checklist (one row per class A-G), or set architectural_shift: false "
                "in the frontmatter when the detection misfired"
            ),
            severity="error",
        )
    ]
