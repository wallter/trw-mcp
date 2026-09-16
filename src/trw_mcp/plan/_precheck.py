"""Advisory ownership precheck (PRD-CORE-275-FR01, FR02).

ADVISORY MEANS ADVISORY. Nothing here writes a file, mutates the manifest,
takes a lock, reserves a path or grants permission. It answers one question —
"who currently DECLARES this path?" — and the answer is context for a human or
an agent to act on, never a decision.

Two limits are stated in the output itself rather than left to documentation,
because an agent reading a bare owner name will otherwise over-trust it:

* declared allocation is not live intent — a member can own a path nobody is
  working on, and unowned does not mean free;
* an absent owner is absence of a declaration, not permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trw_mcp.formation import FormationError, FormationManifest, Ownership, owner_of_manifest, relative_to_root
from trw_mcp.plan._schema import PlanError, PlanRefusal, check_path

#: Printed with every precheck. See the module docstring for why it is output
#: rather than documentation.
ADVISORY_NOTE = (
    "Advisory only. Declared allocation is not live intent: an owned path may be idle, "
    "and an unowned path is an absent declaration, not permission."
)


@dataclass(frozen=True)
class PrecheckRow:
    """One path and what the manifest declares about it."""

    path: str
    member_id: str | None
    glob: str | None
    is_test_path: bool
    ambiguous: bool = False

    def render(self) -> str:
        """One line, and deliberately TAB-FREE.

        A rendered row becomes a review finding, and findings are refused if they
        carry a control character — a tab is one (0x09). That rule exists so a
        hostile peer cannot embed a newline and forge a line that looks like this
        tool's own output. Alignment is not worth carving an exception in it.
        """
        if self.ambiguous:
            return f"{self.path} :: AMBIGUOUS :: competing equal-length declarations"
        if self.member_id is None:
            return f"{self.path} :: - :: no declaration"
        kind = "test" if self.is_test_path else "src"
        return f"{self.path} :: {self.member_id} :: {self.glob} [{kind}]"


def _resolve(manifest: FormationManifest, path: str, project_root: Path) -> PrecheckRow:
    check_path(path)
    # The pre-filter is load-bearing and is NOT inherited from owner_of.
    # relative_to_root returns None for a path outside the project and owner_of
    # then reports it UNOWNED by documented fail-silent design. This slice
    # refuses instead, so "outside the project" can never be read as "free".
    if relative_to_root(path, project_root) is None:
        raise PlanError(PlanRefusal.TRAVERSAL_PATH, f"{path!r} does not resolve inside the project")
    try:
        ownership: Ownership = owner_of_manifest(manifest, path, project_root)
    except FormationError:
        # Two members declare this path with equal-length globs. Reporting a
        # winner would invent an answer the manifest does not contain.
        return PrecheckRow(path=path, member_id=None, glob=None, is_test_path=False, ambiguous=True)
    return PrecheckRow(
        path=ownership.path,
        member_id=ownership.member_id,
        glob=ownership.glob,
        is_test_path=ownership.is_test_path,
    )


def precheck(manifest: FormationManifest, paths: list[str], project_root: Path) -> list[PrecheckRow]:
    """Resolve every path. Refuses the whole batch on the first bad input.

    All-or-nothing on purpose: a partially-checked plan invites acting on the
    half that resolved while the refused half is the interesting one.
    """

    return [_resolve(manifest, path, project_root) for path in paths]
