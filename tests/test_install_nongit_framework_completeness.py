"""PRD-INFRA-170-FR06: non-git installs must not be silent half-installs.

The reproduced incident: a fresh ``curl … | bash`` into a directory that is not
(yet) a git repo wrote ``.trw/config.yaml`` + ``.mcp.json`` but SKIPPED the
framework-body deploy (init-project step 9), leaving
``.trw/frameworks/{FRAMEWORK-CORE.md, AARE-F-CORE.md, AARE-F-REFERENCE.md,
VERSION.yaml, DEPLOYMENT.json}`` missing while every pre-release check passed
green.

These tests lock the post-FR06 behavior at the ``init_project`` seam (the only
step that deploys the framework bodies):

- a non-git target STILL gets every framework body deployed non-empty;
- ``trw-mcp doctor``'s ``framework_integrity`` check reports no FAIL afterward;
- the non-git deploy is idempotent on re-run;
- a loud, non-silent warning names the non-git condition (never a silent skip);
- a real git repo keeps its existing behavior (no non-git warning).

They also assert the shell/installer bootstraps no longer redirect the
init/verify invocation to the null device and run ``doctor`` at install end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.models.config import get_config
from trw_mcp.server._doctor_framework_integrity import check_framework_integrity

# The five framework bodies whose absence was the reproduced silent half-install.
_FRAMEWORK_BODIES = (
    "FRAMEWORK-CORE.md",
    "AARE-F-CORE.md",
    "AARE-F-REFERENCE.md",
    "VERSION.yaml",
    "DEPLOYMENT.json",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bodies_present_nonempty(target: Path) -> list[str]:
    """Return the subset of framework bodies that are absent or zero-length."""
    frameworks = target / ".trw" / "frameworks"
    missing: list[str] = []
    for body in _FRAMEWORK_BODIES:
        path = frameworks / body
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(body)
    return missing


def _doctor_framework_status(target: Path) -> tuple[str, str]:
    config = get_config()
    return check_framework_integrity(
        target,
        framework_version=config.framework_version,
        aaref_version=config.aaref_version,
    )


def test_non_git_target_deploys_all_framework_bodies(tmp_path: Path) -> None:
    """A directory with no ``.git`` still receives every framework body."""
    target = tmp_path / "scratch"
    target.mkdir()
    assert not (target / ".git").exists()  # sanity: genuinely non-git

    result = init_project(target)

    assert result["errors"] == []
    assert _bodies_present_nonempty(target) == []


def test_non_git_install_leaves_no_doctor_fail(tmp_path: Path) -> None:
    """After a non-git init, ``doctor`` framework_integrity is not FAIL."""
    target = tmp_path / "scratch"
    target.mkdir()

    init_project(target)

    status, message = _doctor_framework_status(target)
    assert status != "FAIL", message


def test_non_git_install_emits_loud_warning(tmp_path: Path) -> None:
    """The non-git condition surfaces a warning — never a silent skip.

    Pre-FR06 the whole init returned an error and deployed nothing. Post-FR06 it
    deploys the framework AND records a non-silent warning naming the condition,
    so the CLI (and any caller) can surface it loudly instead of /dev/null.
    """
    target = tmp_path / "scratch"
    target.mkdir()

    result = init_project(target)

    warnings = result.get("warnings", [])
    assert any("not a git repository" in w for w in warnings), warnings
    # The pre-FR06 fatal error path must be gone.
    assert not any("not a git repository" in e for e in result["errors"])


def test_non_git_deploy_is_idempotent(tmp_path: Path) -> None:
    """Re-running init in the same non-git dir does not corrupt the bodies."""
    target = tmp_path / "scratch"
    target.mkdir()

    # VERSION.yaml + DEPLOYMENT.json carry a fresh timestamp each run; the three
    # compiled markdown bodies are the content and must be byte-stable.
    stable_bodies = ("FRAMEWORK-CORE.md", "AARE-F-CORE.md", "AARE-F-REFERENCE.md")

    init_project(target)
    first = {body: (target / ".trw" / "frameworks" / body).read_bytes() for body in stable_bodies}

    # Second run must not error, must keep every body present + non-empty.
    result = init_project(target)
    assert result["errors"] == []
    assert _bodies_present_nonempty(target) == []

    # Compiled content bodies are byte-stable across re-runs (no corruption).
    for body, data in first.items():
        assert (target / ".trw" / "frameworks" / body).read_bytes() == data

    status, _ = _doctor_framework_status(target)
    assert status != "FAIL"


def test_git_repo_has_no_non_git_warning(tmp_path: Path) -> None:
    """A real git repo keeps existing behavior: no non-git warning."""
    target = tmp_path / "repo"
    (target / ".git").mkdir(parents=True)  # minimal repo marker

    result = init_project(target)

    assert result["errors"] == []
    assert _bodies_present_nonempty(target) == []
    assert not any("not a git repository" in w for w in result.get("warnings", []))


# ── Bootstrap-surface guards (grep-level; the Docker e2e lives in FR01) ──────


def test_install_sh_does_not_null_redirect_init_invocation() -> None:
    """FR06: the init/verify invocation in install.sh is not silenced.

    A ``>/dev/null`` on the framework-deploy call is exactly what made the
    reproduced failure invisible.
    """
    script = (_REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "init-project ." in stripped and "trw-mcp init-project" in stripped:
            assert "/dev/null" not in stripped, f"init invocation still silenced: {stripped}"


def test_install_sh_runs_doctor_at_install_end() -> None:
    """FR06: install.sh runs ``doctor`` at the end and warns loudly on FAIL."""
    script = (_REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "trw-mcp doctor" in script
    assert '"status": "FAIL"' in script or "status.*FAIL" in script


@pytest.mark.parametrize("needle", ["skipping project setup"])
def test_template_no_longer_skips_framework_on_non_git(needle: str) -> None:
    """FR06: install-trw.template.py no longer bails the whole setup on non-git.

    The pre-FR06 guard returned ``[]`` (skipping the framework deploy) when
    ``.git`` was absent. Post-FR06 the non-git branch must fall through to the
    real init so the framework deploys.
    """
    template = (_REPO_ROOT / "trw-mcp" / "scripts" / "install-trw.template.py").read_text(encoding="utf-8")
    # The old skip-and-return message must be gone from the non-git branch.
    assert needle not in template
    # And a doctor run must exist at install end.
    assert "trw-mcp doctor" in template or "doctor" in template
