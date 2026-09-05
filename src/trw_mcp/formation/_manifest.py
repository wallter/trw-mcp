"""Typed formation manifest — the single source of truth for a formation (FR01).

A *formation* is one orchestrating session plus N peer sessions, each running
its own ``trw_init``. This module owns the artifact that links them: member
identity, path ownership, PRD-id allocation, and member lifecycle state.

WHY A CLOSED MODEL, AND WHY THE REFUSALS ARE HERE. The artifact this replaces
was prose — a hand-typed ownership table in an orchestrator's plan file — and
prose cannot refuse a contradiction. Every validator below encodes a collision
that has actually cost work in this repository: two members claiming the same
glob, two members allocating the same PRD-id block (PRD-CORE-256, twice on
2026-09-04), a member id written twice with different ownership. They are
refusals rather than warnings because a coordination artifact that describes an
impossible state is worse than none: every downstream adapter would then have to
invent a tie-break, and four adapters inventing three tie-breaks is the defect
class this package exists to close.

WHAT IS *NOT* VALIDATED HERE, AND WHY. Whether a ``prd_ids`` entry already names
a PRD file on disk is an ALLOCATION-time question, checked by
:func:`trw_mcp.formation.create`. It deliberately does not run on every load:
once a member writes the PRD it was allocated, that file exists, and a load-time
refusal would take the whole formation down at exactly the moment it started
working. The structural half — the same id in two members' blocks — is checked
here, on every load, because it can never become legitimate.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "TERMINAL_STATUSES",
    "FormationError",
    "FormationManifest",
    "FormationMember",
    "FormationMemberStatus",
    "normalise_glob",
]


class FormationError(RuntimeError):
    """Raised for every refusal this package makes.

    One exception type on purpose: each of the four adapters catches exactly
    this and reports its message verbatim, so an operator sees the same sentence
    at the commit boundary, in the hook, in the status roll-up, and at the
    deliver gate rather than four paraphrases of one condition.
    """


class FormationMemberStatus(str, Enum):
    """Closed member lifecycle vocabulary.

    ``pending``    declared by the orchestrator, has not joined
    ``joined``     joined; its run path and pin are recorded
    ``active``     joined and reporting work (set by the member's own run state)
    ``delivered``  the member's own ``trw_deliver`` succeeded
    ``abandoned``  the orchestrator recorded that this member will not finish
    ``reassigned`` the orchestrator moved this member's scope elsewhere

    The last three are the FROZEN terminal set the FR11 deliver gate reads. It
    is frozen so that adding a member state later cannot silently widen what
    counts as "finished" — a new state is non-terminal until someone puts it in
    this set deliberately.
    """

    PENDING = "pending"
    JOINED = "joined"
    ACTIVE = "active"
    DELIVERED = "delivered"
    ABANDONED = "abandoned"
    REASSIGNED = "reassigned"


#: The frozen terminal set. See :class:`FormationMemberStatus`.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        FormationMemberStatus.DELIVERED.value,
        FormationMemberStatus.ABANDONED.value,
        FormationMemberStatus.REASSIGNED.value,
    }
)

#: ``member_id`` / ``formation_id`` shape. Deliberately narrow: these strings are
#: interpolated into brief text, warning messages, and file names, so anything
#: that could be read as a path segment, a shell metacharacter, or a newline is
#: refused at the boundary rather than escaped at every use site (NFR03).
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: PRD identifier shape, matching the repository convention
#: ``PRD-{CATEGORY}-{SEQUENCE}`` documented in ``CLAUDE.md``.
_PRD_ID_RE = re.compile(r"^PRD-[A-Z0-9]+-[0-9]+$")


def normalise_glob(raw: str) -> str:
    """Return *raw* as a clean repo-relative glob, or raise naming the offender.

    Lexical only — never ``realpath``, never a stat. A glob is inside the
    project root exactly when it is relative and contains no ``..`` segment, so
    the containment property NFR03 requires is decided from the string itself
    and cannot be changed by a symlink between validation and use.
    """
    text = raw.strip()
    if not text:
        raise FormationError("owned path glob is empty")
    if "\n" in text or "\r" in text or "\x00" in text:
        raise FormationError(f"owned path glob contains a control character: {text!r}")
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise FormationError(f"owned path glob must be repo-relative, not absolute: {text!r}")
    if text.startswith("~"):
        raise FormationError(f"owned path glob must be repo-relative, not home-relative: {text!r}")
    cleaned = text.removeprefix("./")
    if any(part == ".." for part in cleaned.split("/")):
        raise FormationError(f"owned path glob escapes the project root: {text!r}")
    return cleaned.rstrip("/")


class FormationMember(BaseModel):
    """One member of a formation.

    ``run_path`` and ``pin_key`` are absent until the member joins (FR04); they
    are the evidence that a declared member became a real session, and nothing
    else may write them.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    member_id: str
    client: str
    role: str = ""
    run_path: str | None = None
    pin_key: str | None = None
    owned_paths: list[str] = Field(default_factory=list)
    test_owned_paths: list[str] = Field(default_factory=list)
    prd_ids: list[str] = Field(default_factory=list)
    status: FormationMemberStatus = FormationMemberStatus.PENDING
    joined_utc: str | None = None
    note: str = ""

    @field_validator("member_id")
    @classmethod
    def _check_member_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"member_id must match {_ID_RE.pattern!r}; got {value!r}")
        return value

    @field_validator("owned_paths", "test_owned_paths")
    @classmethod
    def _check_globs(cls, value: list[str]) -> list[str]:
        return [normalise_glob(item) for item in value]

    @field_validator("prd_ids")
    @classmethod
    def _check_prd_ids(cls, value: list[str]) -> list[str]:
        for item in value:
            if not _PRD_ID_RE.match(item.strip()):
                raise ValueError(f"prd_ids entry must look like PRD-CATEGORY-NNN; got {item!r}")
        return [item.strip() for item in value]


class FormationManifest(BaseModel):
    """The formation artifact, written as ``formation.yaml`` under the
    orchestrator's run directory.

    ``revision`` is monotonic and is bumped by exactly one on every accepted
    mutation, so a concurrency test can assert "N joins advanced it by N" and an
    operator can tell two manifests apart without diffing them.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    formation_id: str
    revision: int = Field(default=1, ge=1)
    created_utc: str
    updated_utc: str
    orchestrator_run_path: str
    shared_rules_ref: str = ""
    members: list[FormationMember] = Field(default_factory=list)

    @field_validator("formation_id")
    @classmethod
    def _check_formation_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError(f"formation_id must match {_ID_RE.pattern!r}; got {value!r}")
        return value

    @field_validator("shared_rules_ref")
    @classmethod
    def _check_shared_rules_ref(cls, value: str) -> str:
        return normalise_glob(value) if value.strip() else ""

    @model_validator(mode="after")
    def _check_cross_member_invariants(self) -> FormationManifest:
        _refuse_duplicate_member_ids(self.members)
        _refuse_overlapping_globs(self.members, "owned_paths")
        _refuse_overlapping_globs(self.members, "test_owned_paths")
        _refuse_shared_prd_ids(self.members)
        return self

    def member(self, member_id: str) -> FormationMember:
        """Return the named member, or raise listing the declared ids."""
        for entry in self.members:
            if entry.member_id == member_id:
                return entry
        declared = ", ".join(m.member_id for m in self.members) or "(none)"
        raise FormationError(f"formation {self.formation_id!r} declares no member {member_id!r}. Declared: {declared}")

    def non_terminal_members(self) -> list[FormationMember]:
        """Members that have joined but reached no terminal status (FR11).

        ``pending`` is excluded on purpose: a member that never joined produced
        no run and therefore no evidence for the orchestrator to be waiting on.
        Blocking on it would make declaring an optional peer a delivery hazard.
        """
        return [m for m in self.members if str(m.status) not in TERMINAL_STATUSES and m.run_path]


def _refuse_duplicate_member_ids(members: list[FormationMember]) -> None:
    seen: set[str] = set()
    for entry in members:
        if entry.member_id in seen:
            raise ValueError(f"duplicate member_id {entry.member_id!r}")
        seen.add(entry.member_id)


def _refuse_overlapping_globs(members: list[FormationMember], field: str) -> None:
    """Refuse the same glob string in two members' lists of *field*.

    Textual equality, not glob-intersection: deciding whether ``src/a/**`` and
    ``src/**`` intersect is undecidable in general for the pattern language
    ``fnmatch`` accepts, and a validator that ANSWERED that question would be
    guessing. The rule this enforces is the one an orchestrator can actually
    satisfy — do not write the same pattern twice — and ``owner_of`` resolves a
    residual nested-pattern ambiguity deterministically by longest match.
    """
    owner_by_glob: dict[str, str] = {}
    for entry in members:
        for glob in getattr(entry, field):
            previous = owner_by_glob.get(glob)
            if previous is not None:
                raise ValueError(
                    f"{field} glob {glob!r} is claimed by both {previous!r} and {entry.member_id!r}; "
                    "one glob may belong to one member"
                )
            owner_by_glob[glob] = entry.member_id


def _refuse_shared_prd_ids(members: list[FormationMember]) -> None:
    owner_by_prd: dict[str, str] = {}
    for entry in members:
        for prd_id in entry.prd_ids:
            previous = owner_by_prd.get(prd_id)
            if previous is not None:
                raise ValueError(
                    f"prd_ids entry {prd_id!r} is allocated to both {previous!r} and {entry.member_id!r}; "
                    "an identifier block may belong to one member"
                )
            owner_by_prd[prd_id] = entry.member_id
