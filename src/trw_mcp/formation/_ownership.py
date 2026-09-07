"""``owner_of`` — which member owns a path (FR09, FR10).

Shared by the two enforcement adapters so the commit boundary and the hook can
never disagree about who owns a file. Both call the facade; neither matches a
glob itself.

MATCHING RULES, STATED ONCE.

* Comparison is on a REPO-RELATIVE POSIX path. An absolute path inside the
  project root is made relative first; an absolute path outside it is UNOWNED,
  because a formation's manifest may only claim paths inside its own repository
  (NFR03) and therefore can say nothing about anything else.
* A glob matches either as an ``fnmatch`` pattern or as a directory prefix, so
  ``trw-mcp/src/trw_mcp/formation`` owns everything beneath it without the
  author having to remember to write ``/**``. Forgetting the suffix is the
  single most likely authoring mistake, and its failure mode would be a silent
  hole in enforcement.
* When several globs match, the LONGEST one wins. Nested ownership
  (``src/**`` for one member, ``src/formation/**`` for another) is a legitimate
  arrangement the manifest cannot refuse textually, and longest-match is the
  only tie-break that makes the more specific declaration mean something.
* An exact tie between two members is a refusal, not a coin flip: it can only
  arise from a manifest that :mod:`._manifest` should have rejected, and
  answering it would hide that.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from trw_mcp.formation._manifest import FormationError, FormationManifest

__all__ = ["Ownership", "owner_of", "relative_to_root"]


@dataclass(frozen=True)
class Ownership:
    """Who owns one path, and the declaration that decided it."""

    path: str
    member_id: str | None
    glob: str | None
    #: True when the match came from ``test_owned_paths`` rather than ``owned_paths``.
    is_test_path: bool = False


def relative_to_root(raw: str, project_root: Path) -> str | None:
    """Return *raw* as a repo-relative POSIX path, or ``None`` when outside.

    Lexical: ``Path.resolve`` is deliberately NOT used, so a symlink cannot move
    a path into or out of ownership between this call and the write it guards.
    """
    text = raw.strip()
    if not text:
        return None
    candidate = PurePosixPath(text.replace("\\", "/"))
    if candidate.is_absolute():
        root = PurePosixPath(project_root.as_posix())
        try:
            candidate = candidate.relative_to(root)
        except ValueError:  # trw-fail-silent-allow: outside the project root IS unowned per the module docstring's matching rules, not a read failure
            return None
    parts = [part for part in candidate.parts if part not in (".", "")]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts) or None


def _glob_matches(glob: str, rel_path: str) -> bool:
    if fnmatchcase(rel_path, glob):
        return True
    # Directory-prefix form: `src/formation` owns `src/formation/_x.py`.
    return rel_path.startswith(glob.rstrip("/") + "/")


def owner_of(manifest: FormationManifest, raw_path: str, project_root: Path) -> Ownership:
    """Resolve the owning member of *raw_path*, or an unowned :class:`Ownership`."""
    rel = relative_to_root(raw_path, project_root)
    if rel is None:
        return Ownership(path=raw_path, member_id=None, glob=None)
    best: Ownership | None = None
    best_len = -1
    for member in manifest.members:
        for is_test, globs in ((False, member.owned_paths), (True, member.test_owned_paths)):
            for glob in globs:
                if not _glob_matches(glob, rel):
                    continue
                if len(glob) > best_len:
                    best = Ownership(path=rel, member_id=member.member_id, glob=glob, is_test_path=is_test)
                    best_len = len(glob)
                elif len(glob) == best_len and best is not None and best.member_id != member.member_id:
                    raise FormationError(
                        f"path {rel!r} is claimed equally by {best.member_id!r} (glob {best.glob!r}) and "
                        f"{member.member_id!r} (glob {glob!r}); resolve the overlap by manifest revision"
                    )
    return best or Ownership(path=rel, member_id=None, glob=None)
