"""PRD-CORE-317: ``trw_prd_validate`` / ``trw-mcp prd validate`` resolve a
PRD's cited paths against the PRD's OWN git worktree, not the MCP server's
project root (the main checkout).

Failing-first: a PRD authored in a git worktree that cites a file existing
only in that worktree used to fail its grounding check when the server's
project root pointed at a different checkout (four groomers hit this in the
R8 swarm; one worked around it by mirroring files into the shared main
checkout). ``resolve_prd_grounding_root`` (``state/validation/_prd_repo_root.py``)
fixes it by preferring the git toplevel containing the PRD file over the
configured project root --- but ONLY when that toplevel already resolves
inside the configured root.

Design history (lead simplification after 3 review rounds each found a new
widening in an earlier, git-aware CONTAINMENT design): containment (can this
caller read prd_path at all) is now completely UNCHANGED from before this
PRD --- exactly ``resolve_project_root()``, no git lookup, no caller
override. Only grounding (where cited paths are searched) moves, and it can
never move outside the already-verified containment root. This closes all
three prior findings by construction rather than by patching each one:
- a caller-controlled override that could bypass containment: there is no
  override parameter anywhere in this design;
- an arbitrary/external git repo trusted for containment: containment never
  consults git;
- a configured root that is a subdirectory of a larger enclosing repo
  widening to the whole repo: grounding requires the discovered toplevel be
  INSIDE project_root, so an enclosing (ancestor) repo can never qualify.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.validation._prd_repo_root import resolve_prd_grounding_root
from trw_mcp.tools._prd_validate_tool import run_prd_validate

pytestmark = pytest.mark.integration

# Class-first census (Q2): the served PRD-validate entrypoints must resolve
# their grounding root through ONE function, never a bare `resolve_project_root()`
# call that would silently re-introduce the main-checkout bug this PRD fixes.
_SERVED_ENTRYPOINT_FILES = (
    Path(__file__).parent.parent / "src/trw_mcp/tools/_prd_validate_tool.py",
    Path(__file__).parent.parent / "src/trw_mcp/tools/_prd_cli.py",
)


def _bare_resolve_project_root_calls(source_path: Path) -> list[int]:
    """Line numbers of any *bare* ``resolve_project_root(...)`` call --- i.e.
    not accessed as ``<something>.resolve_project_root`` --- in *source_path*.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "resolve_project_root":
            hits.append(node.lineno)
    return hits


class TestServedEntrypointCensus:
    """Containment in ``run_prd_validate`` is the only place a served
    entrypoint may call ``resolve_project_root()`` directly (that call IS
    the trust boundary, unchanged from before this PRD); no OTHER bare call
    may exist, or a second, inconsistent containment root could creep in."""

    @pytest.mark.parametrize("source_path", _SERVED_ENTRYPOINT_FILES, ids=lambda p: p.name)
    def test_at_most_one_bare_resolve_project_root_call(self, source_path: Path) -> None:
        hits = _bare_resolve_project_root_calls(source_path)
        assert len(hits) <= 1, (
            f"{source_path.name} calls resolve_project_root() directly at line(s) {hits}; "
            "containment must have exactly one call site"
        )


_PRD_TEMPLATE = """\
---
prd:
  id: PRD-TEST-{seq}
  title: "Worktree-grounding fixture PRD"
  version: "1.0"
  status: draft
  priority: P1
  category: CORE

ip_tier: public
functionality_level: live
stubs: []
---

# PRD-TEST-{seq}: Worktree-grounding fixture

## 1. Problem Statement

A concrete implementation path lives only in the authoring worktree.

## 3. Functional Requirements

### FR01 — Worktree-only file reference
**Priority**: Must Have

surface: internal

Modify `{ref}` as part of this change.
"""


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture()
def prd_worktree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A main checkout plus a linked worktree NESTED inside it (mirroring the
    R8 swarm's actual layout: ``.claude/worktrees/<name>`` and
    ``.trw/worktrees/<name>`` both live inside the main checkout), each
    carrying the SAME PRD text.

    Returns ``(main_root, worktree_root, prd_in_main, prd_in_worktree)``. The
    file the PRD cites exists ONLY under ``worktree_root`` --- validating the
    main-checkout copy must find it hallucinated; validating the worktree
    copy must not, because each PRD's own git toplevel differs.
    """
    main_root = tmp_path / "main"
    main_root.mkdir()
    _run_git(["init", "-q"], main_root)
    _run_git(["config", "user.email", "test@example.com"], main_root)
    _run_git(["config", "user.name", "Test"], main_root)
    (main_root / "README.md").write_text("main\n", encoding="utf-8")
    _run_git(["add", "README.md"], main_root)
    _run_git(["commit", "-q", "-m", "init"], main_root)

    # Nested inside main_root, like .claude/worktrees/<name>.
    worktree_root = main_root / "worktrees" / "wt"
    _run_git(["worktree", "add", "-q", "-b", "prd-wt", str(worktree_root)], main_root)

    worktree_only_file = worktree_root / "src" / "only_in_worktree.py"
    worktree_only_file.parent.mkdir(parents=True)
    worktree_only_file.write_text("# lives only in the worktree\n", encoding="utf-8")

    prd_text = _PRD_TEMPLATE.format(seq="317", ref="src/only_in_worktree.py")
    prd_in_main = main_root / "PRD-TEST-317.md"
    prd_in_main.write_text(prd_text, encoding="utf-8")
    prd_in_worktree = worktree_root / "PRD-TEST-317.md"
    prd_in_worktree.write_text(prd_text, encoding="utf-8")
    return main_root, worktree_root, prd_in_main, prd_in_worktree


class TestResolvePrdGroundingRoot:
    """Unit coverage for the grounding resolver itself."""

    def test_non_git_directory_falls_back(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        prd_path = plain_dir / "PRD.md"
        result = resolve_prd_grounding_root(prd_path, project_root=tmp_path)
        assert result == tmp_path

    def test_nested_worktree_is_used(self, prd_worktree: tuple[Path, Path, Path, Path]) -> None:
        main_root, worktree_root, _prd_in_main, prd_in_worktree = prd_worktree
        result = resolve_prd_grounding_root(prd_in_worktree, project_root=main_root)
        assert result == worktree_root.resolve()


class TestPrdValidateGroundsAgainstOwnWorktree:
    """Failing-first: validate the SAME PRD text via the served entrypoint,
    once from each git worktree it lives in. Pre-fix, the tool always
    grounded against the server's project root, so a PRD authored in a
    nested scratchpad worktree false-failed grounding for paths that exist
    only there.
    """

    def test_main_checkout_copy_hallucinates_the_worktree_only_path(
        self, prd_worktree: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The file the PRD cites does not exist under the main checkout, so
        grounding it there (its own git toplevel) correctly flags it."""
        main_root, _worktree_root, prd_in_main, _prd_in_worktree = prd_worktree
        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: main_root)

        result = run_prd_validate(prd_path=str(prd_in_main))

        readiness = next(d for d in result["dimensions"] if d["name"] == "implementation_readiness")
        assert readiness["details"].get("hallucinated_paths", 0) > 0, (
            "expected the worktree-only path to be flagged hallucinated when validated from the main checkout"
        )

    def test_worktree_copy_resolves_cleanly(
        self,
        prd_worktree: tuple[Path, Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The configured project root is the main checkout (the worktree is
        nested inside it, so containment admits the worktree PRD unchanged);
        grounding then prefers the worktree's own toplevel, so the
        worktree-only reference resolves cleanly."""
        main_root, _worktree_root, _prd_in_main, prd_in_worktree = prd_worktree

        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: main_root)

        result = run_prd_validate(prd_path=str(prd_in_worktree))

        readiness = next(d for d in result["dimensions"] if d["name"] == "implementation_readiness")
        assert readiness["details"].get("hallucinated_paths", 0) == 0, (
            "the PRD's own worktree should ground `src/only_in_worktree.py` without penalty"
        )


class TestNoOverrideParameterExists:
    """Sol round-1 finding (a caller-controlled override bypassing
    containment) is impossible by construction in the simplified design:
    there is no override parameter anywhere in ``run_prd_validate``."""

    def test_run_prd_validate_has_no_repo_root_parameter(self) -> None:
        signature = inspect.signature(run_prd_validate)
        assert "repo_root" not in signature.parameters

    def test_unknown_keyword_argument_is_rejected(self, tmp_path: Path) -> None:
        prd_path = tmp_path / "PRD.md"
        prd_path.write_text("x", encoding="utf-8")
        with pytest.raises(TypeError):
            run_prd_validate(prd_path=str(prd_path), repo_root=str(tmp_path))  # type: ignore[call-arg]


class TestExternalRepoNeverWidensGrounding:
    """Sol round-2 finding (an arbitrary/unrelated git repo trusted for
    containment) re-expressed for the simplified design: containment never
    consults git at all, so an external repo elsewhere on disk cannot affect
    which files may be read, regardless of what grounding later does."""

    def test_unrelated_git_repo_fails_containment_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The full served path: a PRD in an unrelated repo, with the
        server's project root elsewhere, must still raise ``StateError`` ---
        containment never consults git, so this is unaffected by grounding."""
        trusted_root = tmp_path / "trusted"
        trusted_root.mkdir()

        unrelated_repo = tmp_path / "unrelated"
        unrelated_repo.mkdir()
        _run_git(["init", "-q"], unrelated_repo)
        _run_git(["config", "user.email", "test@example.com"], unrelated_repo)
        _run_git(["config", "user.name", "Test"], unrelated_repo)
        prd_path = unrelated_repo / "PRD.md"
        prd_path.write_text(_PRD_TEMPLATE.format(seq="900", ref="src/x.py"), encoding="utf-8")

        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: trusted_root)

        with pytest.raises(StateError, match="outside the project root"):
            run_prd_validate(prd_path=str(prd_path))


class TestConfiguredSubdirectoryNeverWidensToEnclosingRepo:
    """Sol round-3 finding (a configured root that is a subdirectory of a
    larger enclosing repo widening to the whole repo) re-expressed for the
    simplified design: grounding requires the discovered toplevel be INSIDE
    project_root, so an ancestor (enclosing) repo's toplevel can never
    qualify --- it fails the ``is_relative_to`` check by construction."""

    def test_enclosing_repo_toplevel_is_not_used_for_grounding(self, tmp_path: Path) -> None:
        enclosing_repo = tmp_path / "monorepo"
        enclosing_repo.mkdir()
        _run_git(["init", "-q"], enclosing_repo)
        _run_git(["config", "user.email", "test@example.com"], enclosing_repo)
        _run_git(["config", "user.name", "Test"], enclosing_repo)
        (enclosing_repo / "README.md").write_text("x\n", encoding="utf-8")
        _run_git(["add", "README.md"], enclosing_repo)
        _run_git(["commit", "-q", "-m", "init"], enclosing_repo)

        # The configured project root is a SUBDIRECTORY of the enclosing
        # repo, not its own toplevel --- but has no git repo of its own, so
        # `git rev-parse --show-toplevel` from inside it resolves UP to the
        # enclosing repo.
        project_root = enclosing_repo / "trw-mcp"
        project_root.mkdir()
        prd_path = project_root / "PRD.md"
        prd_path.write_text("x", encoding="utf-8")

        result = resolve_prd_grounding_root(prd_path, project_root=project_root)

        assert result == project_root, (
            "the enclosing repo's toplevel must never be used for grounding when it falls "
            "outside (above) the configured project root"
        )


class TestGroundingLookupRespectsBudget:
    """VALIDATE-ROOT-BUDGET-SKIP: the git-toplevel lookup is repo-grounded work,
    so ``fast=True`` skips it and the PRD-FIX-112 deadline bounds it. Pre-fix it
    ran unconditionally (up to its own 5s timeout) before the deadline existed.
    """

    @staticmethod
    def _forbid_git(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        calls: list[Path] = []

        def _record(prd_dir: Path, **_kwargs: object) -> Path | None:
            calls.append(prd_dir)
            return None

        monkeypatch.setattr("trw_mcp.state.validation._prd_repo_root._git_toplevel", _record)
        return calls

    def test_fast_mode_skips_git_lookup(
        self, prd_worktree: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        main_root, _worktree_root, _prd_in_main, prd_in_worktree = prd_worktree
        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: main_root)
        calls = self._forbid_git(monkeypatch)

        result = run_prd_validate(prd_path=str(prd_in_worktree), fast=True)

        assert calls == []
        assert result["validation_partial"] is True

    def test_spent_budget_skips_git_lookup(
        self, prd_worktree: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.models.config import get_config

        main_root, _worktree_root, _prd_in_main, prd_in_worktree = prd_worktree
        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", lambda: main_root)
        monkeypatch.setattr(get_config(), "prd_validate_budget_seconds", 0.0)
        calls = self._forbid_git(monkeypatch)

        result = run_prd_validate(prd_path=str(prd_in_worktree))

        assert calls == []
        assert result["validation_partial"] is True

    def test_expired_deadline_returns_configured_root(
        self, prd_worktree: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        main_root, _worktree_root, _prd_in_main, prd_in_worktree = prd_worktree
        calls = self._forbid_git(monkeypatch)

        result = resolve_prd_grounding_root(prd_in_worktree, project_root=main_root, deadline=time.monotonic() - 1.0)

        assert result == main_root
        assert calls == []

    def test_git_timeout_never_outlasts_the_deadline(
        self, prd_worktree: tuple[Path, Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        main_root, worktree_root, _prd_in_main, prd_in_worktree = prd_worktree
        timeouts: list[float] = []
        real_run = subprocess.run

        def _spy(*args: object, **kwargs: object) -> object:
            timeouts.append(float(kwargs["timeout"]))  # type: ignore[arg-type]
            return real_run(*args, **kwargs)  # type: ignore[call-overload]

        monkeypatch.setattr("trw_mcp.state.validation._prd_repo_root.subprocess.run", _spy)

        result = resolve_prd_grounding_root(prd_in_worktree, project_root=main_root, deadline=time.monotonic() + 3.0)

        assert result == worktree_root.resolve()
        assert len(timeouts) == 1
        assert 0 < timeouts[0] <= 3.0
