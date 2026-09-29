"""PRD-CORE-321 FR01: the requirement baseline is resolved from git by PRD ID.

Every test builds a throwaway repository under ``tmp_path`` with fixed commit
dates and identities, and imports the module under test inside its body so an
absent module fails one test, not collection of the file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PRDS = "docs/prds"

_ISOLATED_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.invalid",
}


class Repo:
    """A throwaway git repository whose commits carry explicit, increasing dates."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.day = 0
        (root / PRDS).mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(_ISOLATED_ENV)
        stamp = f"2026-01-{self.day + 1:02d}T12:00:00+00:00"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
        result = subprocess.run(["git", *args], cwd=self.root, env=env, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def write(self, name: str, status: str, criterion: str = "strict", *, extra: str = "") -> Path:
        path = self.root / PRDS / name
        path.write_text(prd_text(status, criterion, extra=extra), encoding="utf-8")
        return path

    def commit(self, message: str) -> str:
        self.git("add", "--all", "--", PRDS)
        self.git("commit", "-q", "--no-gpg-sign", "-m", message, "--", PRDS)
        self.day += 1
        return self.git("rev-parse", "HEAD")


def prd_text(status: str, criterion: str, *, extra: str = "") -> str:
    return (
        "---\n"
        "prd:\n"
        "  id: PRD-X-001\n"
        f"  status: {status}\n"
        f"{extra}"
        "  verification:\n"
        "    mappings:\n"
        "      - requirement_id: PRD-X-001-FR01\n"
        "        acceptance_criteria:\n"
        f"          - {criterion}\n"
        "        evidence_artifact: tests/test_x.py\n"
        "---\n"
        "# PRD-X-001\n\n"
        "| Req | Source | Call chain |\n"
        "|-----|--------|------------|\n"
        "| FR01 | US-1 | `pkg.mod.entry` -> `pkg.mod.leaf` |\n"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "repo")


def _kinds(resolution: object) -> list[str]:
    return [finding.kind for finding in resolution.findings]  # type: ignore[attr-defined]


def test_baseline_is_the_approval_commit_with_its_exact_mappings(repo: Repo) -> None:
    from trw_mcp.state.validation.chain_declarations import ChainDeclaration
    from trw_mcp.state.validation.requirement_baseline import BaselineRequirement, resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "draft")
    repo.commit("draft")
    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")
    path = repo.write("PRD-X-001-a.md", "approved", "weak")
    repo.commit("edit")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.status == "resolved"
    assert resolution.baseline_sha == approval
    assert resolution.baseline_date == "2026-01-02"
    assert resolution.approval_shas == (approval,)
    assert resolution.requirements == (
        BaselineRequirement(
            requirement_id="PRD-X-001-FR01",
            acceptance_criteria=("strict",),
            evidence_artifact="tests/test_x.py",
            call_chain=ChainDeclaration(chain=("pkg.mod.entry", "pkg.mod.leaf")),
        ),
    )
    assert dict(resolution.chains) == {"PRD-X-001-FR01": ChainDeclaration(chain=("pkg.mod.entry", "pkg.mod.leaf"))}
    assert resolution.findings == ()


def test_rename_and_rewrite_keeps_original_approval(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "draft")
    repo.commit("draft")
    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")
    repo.git("mv", "--", f"{PRDS}/PRD-X-001-a.md", f"{PRDS}/PRD-X-001-b.md")
    path = repo.write("PRD-X-001-b.md", "approved", "weak")
    rewritten = "".join(
        f"Rewritten paragraph {n}: new prose that shares nothing with the approved text.\n" for n in range(60)
    )
    path.write_text(path.read_text(encoding="utf-8") + rewritten, encoding="utf-8")  # below git's 50% rename similarity
    repo.commit("rename and weaken")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.status == "resolved"
    assert resolution.baseline_sha == approval
    assert resolution.baseline_path == f"{PRDS}/PRD-X-001-a.md"
    assert resolution.requirements[0].acceptance_criteria == ("strict",)


def test_reapproval_keeps_first_approval_commit(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import BaselineFinding, resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved")
    first = repo.commit("approve A1")
    repo.write("PRD-X-001-a.md", "draft", "loosened")
    repo.commit("back to draft")
    path = repo.write("PRD-X-001-a.md", "approved", "loosened")
    newest = repo.commit("approve A2")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.status == "resolved"
    assert resolution.baseline_sha == first
    assert resolution.approval_shas == (first, newest)
    assert resolution.requirements[0].acceptance_criteria == ("strict",)
    assert resolution.findings == (BaselineFinding("baseline_reapproved", first_sha=first, newest_sha=newest),)


def test_partial_then_implemented_with_narrowed_criterion_keeps_first_approval(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved", "all inputs rejected")
    first = repo.commit("approve")
    repo.write("PRD-X-001-a.md", "partial", "all inputs rejected")
    repo.commit("partial")
    path = repo.write("PRD-X-001-a.md", "implemented", "some inputs rejected")
    newest = repo.commit("implemented, narrowed")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.baseline_sha == first
    assert resolution.requirements[0].acceptance_criteria == ("all inputs rejected",)
    assert _kinds(resolution) == ["baseline_reapproved"]
    assert (resolution.findings[0].first_sha, resolution.findings[0].newest_sha) == (first, newest)
    assert resolution.implemented_in_history is True


def test_delete_and_restore_is_one_approval_commit(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")
    (repo.root / PRDS / "PRD-X-001-a.md").unlink()
    repo.commit("delete")
    path = repo.write("PRD-X-001-a.md", "approved", "restored")
    repo.commit("restore")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.approval_shas == (approval,)
    assert resolution.baseline_sha == approval
    assert resolution.findings == ()


def test_id_boundary_excludes_longer_sequence_number(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-0010-other.md", "approved", "other")
    repo.commit("sibling approved first")
    path = repo.write("PRD-X-001-a.md", "draft")
    repo.commit("draft")
    repo.write("PRD-X-0010-other.md", "draft", "other edited")
    repo.commit("sibling edited")
    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.approval_shas == (approval,)
    assert resolution.findings == ()
    sibling = resolve_requirement_baseline("PRD-X-0010", repo.root / PRDS / "PRD-X-0010-other.md")
    assert sibling.requirements[0].acceptance_criteria == ("other",)


@pytest.mark.parametrize("working_status", ["draft", "partial", "in_progress"])
def test_uncommitted_status_regression_keeps_baseline(repo: Repo, working_status: str) -> None:
    from trw_mcp.state.validation.requirement_baseline import BaselineFinding, resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")
    path = repo.write("PRD-X-001-a.md", working_status, "weak")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.status == "resolved"
    assert resolution.baseline_sha == approval
    assert resolution.current_status == working_status
    assert resolution.findings == (BaselineFinding("status_regressed", current_status=working_status),)


@pytest.mark.parametrize(
    "content",
    [
        "no frontmatter at all\n",
        "---\nstatus: [unclosed\n---\n",
        "---\ntitle: no status key\n---\n",
        "---\nstatus: 3\n---\n",
    ],
)
def test_unparseable_current_frontmatter(repo: Repo, content: str) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved")
    repo.commit("approve")
    path = repo.root / PRDS / "PRD-X-001-a.md"
    path.write_text(content, encoding="utf-8")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "current_unparseable")
    assert resolution.baseline_sha is None


def test_no_git_is_named(tmp_path: Path) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = tmp_path / "PRD-X-001-a.md"
    path.write_text(prd_text("approved", "strict"), encoding="utf-8")
    (tmp_path / ".git").write_text("not a repository\n", encoding="utf-8")  # stop discovery at tmp_path

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "no_git")


def test_two_matching_paths_in_one_commit_is_ambiguous(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = repo.write("PRD-X-001-a.md", "approved")
    repo.write("PRD-X-001-copy.md", "approved")
    repo.commit("two files, one id")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "ambiguous_id")


def test_staggered_duplicate_id_is_ambiguous(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = repo.write("PRD-X-001-a.md", "approved")
    repo.commit("first file")
    repo.write("PRD-X-001-b.md", "draft", "a different promise")
    repo.commit("second file with the same id, first one kept")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "ambiguous_id")


def test_a_directory_carrying_the_id_is_not_a_duplicate(repo: Repo) -> None:
    """core321-s1 r2 P2: a PRD-X-001-assets/ directory beside the PRD is not a second PRD file."""
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = repo.write("PRD-X-001-a.md", "approved")
    assets = repo.root / PRDS / "PRD-X-001-assets"
    assets.mkdir()
    (assets / "diagram.txt").write_text("boxes\n", encoding="utf-8")
    approval = repo.commit("approve, with an assets directory")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("resolved", None)
    assert resolution.baseline_sha == approval


def test_uncommitted_duplicate_id_in_working_tree_is_ambiguous(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = repo.write("PRD-X-001-a.md", "approved")
    repo.commit("approve")
    repo.write("PRD-X-001-copy.md", "draft")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "ambiguous_id")


@pytest.mark.parametrize("name", ['PRD-X-001-"quoted".md', "PRD-X-001-caf\u00e9.md", "PRD-X-001-tab\there.md"])
def test_git_quoted_filename_is_read_back_literally(repo: Repo, name: str) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write(name, "draft")
    repo.commit("draft")
    path = repo.write(name, "approved")
    approval = repo.commit("approve")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.baseline_sha) == ("resolved", approval)
    assert resolution.baseline_path == f"{PRDS}/{name}"
    assert resolution.requirements[0].acceptance_criteria == ("strict",)


def test_unreadable_historical_blob_is_named_not_empty(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved")
    approval = repo.commit("approve")
    path = repo.write("PRD-X-001-a.md", "approved", "edited")
    repo.commit("edit")
    blob = repo.git("rev-parse", f"{approval}:{PRDS}/PRD-X-001-a.md")
    (repo.root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()  # the approved version's blob is gone

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "version_unreadable")
    assert resolution.baseline_sha is None


def test_safety_critical_only_in_older_version_is_carried(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "approved", extra="  safety_critical: true\n")
    repo.commit("approve, safety critical")
    path = repo.write("PRD-X-001-a.md", "approved", extra="  safety_critical: false\n")
    repo.commit("flag lowered")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert resolution.safety_critical_in_history is True
    assert resolution.implemented_in_history is False


def test_safety_flag_on_a_draft_version_only_is_not_carried(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-X-001-a.md", "draft", extra="  safety_critical: true\n")
    repo.commit("draft, safety critical")
    path = repo.write("PRD-X-001-a.md", "approved")
    repo.commit("approve without the flag")

    assert resolve_requirement_baseline("PRD-X-001", path).safety_critical_in_history is False


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("untracked_approved", ("baseline_unresolvable", "untracked")),
        ("committed_draft_then_approved_in_worktree", ("baseline_unresolvable", "approval_uncommitted")),
        ("draft_only", ("not_applicable", None)),
    ],
)
def test_no_approval_commit_outcomes(repo: Repo, setup: str, expected: tuple[str, str | None]) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    repo.write("PRD-Y-002-anchor.md", "draft")
    repo.commit("unrelated first commit")
    if setup == "untracked_approved":
        path = repo.write("PRD-X-001-a.md", "approved")
    else:
        path = repo.write("PRD-X-001-a.md", "draft")
        repo.commit("draft")
        if setup == "committed_draft_then_approved_in_worktree":
            repo.write("PRD-X-001-a.md", "approved")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == expected


def test_unborn_branch_untracked_approved_file(repo: Repo) -> None:
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline

    path = repo.write("PRD-X-001-a.md", "approved")

    resolution = resolve_requirement_baseline("PRD-X-001", path)

    assert (resolution.status, resolution.reason) == ("baseline_unresolvable", "untracked")


def test_shallow_clone_reasons(repo: Repo, tmp_path: Path) -> None:
    from trw_mcp.state.validation.requirement_baseline import BaselineFinding, resolve_requirement_baseline

    repo.write("PRD-Y-002-anchor.md", "draft")
    repo.commit("unrelated first commit")
    repo.write("PRD-X-001-a.md", "draft")
    repo.commit("draft")
    repo.write("PRD-X-001-a.md", "approved")
    repo.commit("approve")
    repo.write("PRD-Z-003-a.md", "draft")
    repo.commit("other prd")

    def shallow(depth: int) -> Path:
        clone = tmp_path / f"depth{depth}"
        repo.git("clone", "-q", "--depth", str(depth), f"file://{repo.root}", str(clone))
        return clone / PRDS

    cut = shallow(1)  # the graft adds every file already approved: the real approval may predate it
    at_cut = resolve_requirement_baseline("PRD-X-001", cut / "PRD-X-001-a.md")
    assert at_cut.status == "resolved"
    assert at_cut.findings == (BaselineFinding("shallow_clone", first_sha=at_cut.baseline_sha or ""),)

    (cut / "PRD-Z-003-a.md").write_text(prd_text("approved", "strict"), encoding="utf-8")
    uncommitted = resolve_requirement_baseline("PRD-Z-003", cut / "PRD-Z-003-a.md")
    assert (uncommitted.status, uncommitted.reason) == ("baseline_unresolvable", "shallow_clone")

    whole = shallow(3)  # still shallow, but the draft version is inside the cut
    assert resolve_requirement_baseline("PRD-X-001", whole / "PRD-X-001-a.md").findings == ()
