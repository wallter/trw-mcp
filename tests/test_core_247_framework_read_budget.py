"""PRD-CORE-247-FR07: the framework read is gated on surface presence and phase-scoped.

The budget assertion measures the sections the hook ACTUALLY NAMES against the
compiled core, so a directive that named a section that does not exist, or that
quietly grew back to the whole document, fails here rather than at a reader.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

if not (_ROOT.parent / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_HOOK_DIRS = (
    _ROOT.parent / ".claude" / "hooks",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks",
)
_CORE = _ROOT / "src" / "trw_mcp" / "data" / "framework-core.md"

#: FR07's bound: the largest phase row (`plan`) measures 6,349 characters against
#: the 35,073-character document. Set at the PRD's stated ceiling, so a section
#: added to a phase row has to be justified against it rather than absorbed.
_PHASE_SCOPE_CEILING_CHARS = 6349

#: Every value ``infer_phase``/``phase_from_events`` can return. The mapping must
#: be TOTAL over this set — an unmapped phase falling through to a whole-document
#: read would silently restore the defect.
_ALL_PHASES = ("none", "early", "plan", "implement", "validate", "review", "deliver", "done")


def _section_chars(core: str, heading: str) -> int:
    """Characters of the ``## <heading>`` section in the compiled core."""
    match = re.search(rf"(?ms)^##\s+{re.escape(heading)}\s*$.*?(?=^##\s|\Z)", core)
    assert match is not None, f"section '{heading}' is named by the hook but absent from the compiled core"
    return len(match.group(0))


def _make_project(tmp_path: Path, hook_dir: Path, label: str) -> Path:
    root = tmp_path / label
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "runtime").mkdir(parents=True, exist_ok=True)
    for name in ("lib-trw.sh", "session-start.sh", "user-prompt-submit.sh"):
        target = hooks / name
        target.write_text((hook_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    return root


def _run_session_start(root: Path, source: str, env_extra: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    env.update({"CLAUDE_PROJECT_DIR": str(root), "TRW_PROJECT_ROOT": str(root)})
    # PRD-FIX-128: an inherited session variable outranks the payload's
    # session_id in trw_pin_key, so the hook would read a latch this fixture
    # never wrote.
    env.pop("TRW_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / "session-start.sh")],
        input=json.dumps({"source": source, "session_id": "budget"}),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    ).stdout


def _named_sections(stdout: str, core: str) -> list[str]:
    """Return the core section headings the emitted directive names."""
    headings = re.findall(r"(?m)^##\s+(.+?)\s*$", core)
    return [heading for heading in headings if heading in stdout]


@pytest.fixture(params=_HOOK_DIRS, ids=lambda p: p.parent.name)
def hook_dir(request: pytest.FixtureRequest) -> Path:
    return Path(request.param)


def test_framework_directive_is_gated_and_phase_scoped(tmp_path: Path, hook_dir: Path) -> None:
    """FR07 acceptance, all three arms."""
    core = _CORE.read_text(encoding="utf-8")

    # (a) Degraded mode detected => no directive at all.
    degraded = _make_project(tmp_path, hook_dir, "degraded")
    # PRD-FIX-128-FR03: the latch is keyed on the session, so simulating "this
    # session already emitted" means writing THIS payload's marker. A latch file
    # at the old project-scoped path is never read again, so writing it here
    # would assert nothing.
    latch = degraded / ".trw" / "runtime" / "degraded-mode" / "budget"
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.write_text("", encoding="utf-8")
    for source in ("resume", "compact", "clear"):
        out = _run_session_start(degraded, source)
        assert "FRAMEWORK-CORE.md" not in out, (
            f"{source} emitted a framework read directive while the surface is known-absent — "
            "that is full instruction cost for zero capability"
        )

    # (b) Not degraded => a phase-scoped directive naming real sections.
    healthy = _make_project(tmp_path, hook_dir, "healthy")
    out = _run_session_start(healthy, "startup")
    assert "FRAMEWORK-CORE.md" in out
    named = _named_sections(out, core)
    assert named, "the directive named no section of the compiled core"
    assert "EXECUTION MODEL SUMMARY" in named

    # (c) The named span measures at or under the budget.
    total = sum(_section_chars(core, heading) for heading in named)
    assert total <= _PHASE_SCOPE_CEILING_CHARS, (
        f"the named sections total {total} characters against the {_PHASE_SCOPE_CEILING_CHARS} ceiling. "
        f"The whole document is {len(core)} characters; scoping is the point. Cut a section from the "
        "phase row or raise the ceiling in this same change with the new measurement."
    )
    assert total < len(core), "a phase-scoped read that names the whole document is not scoped"


def test_every_phase_row_stays_within_the_budget(hook_dir: Path) -> None:
    """FR07: the phase-to-section mapping is TOTAL and every row fits.

    Read out of the shipped hook rather than restated here — a second copy of the
    table in the test would pass while the hook's own table drifted.
    """
    core = _CORE.read_text(encoding="utf-8")
    hook = (hook_dir / "session-start.sh").read_text(encoding="utf-8")
    body = hook.split("_framework_sections_for_phase() {", 1)[1].split("\n}", 1)[0]
    assert "*)" in body, "the mapping must have a catch-all row so an unmapped phase cannot fall through"

    for phase in _ALL_PHASES:
        result = subprocess.run(
            ["sh", "-c", f'_framework_sections_for_phase() {{{body}\n}}\n_framework_sections_for_phase "{phase}"'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        named = [part.strip() for part in result.stdout.split(",") if part.strip()]
        assert named, f"phase {phase} resolved to no sections"
        total = sum(_section_chars(core, heading) for heading in named)
        assert total <= _PHASE_SCOPE_CEILING_CHARS, (
            f"phase {phase} names {total} characters, over the {_PHASE_SCOPE_CEILING_CHARS} ceiling"
        )


def test_framework_read_scope_full_restores_the_whole_document_directive(tmp_path: Path, hook_dir: Path) -> None:
    """FR07: ``framework_read_scope: full`` is a real, honest lever.

    A tunable nothing reads is worse than no tunable, so this drives the config
    file rather than asserting the field exists.
    """
    root = _make_project(tmp_path, hook_dir, "fullscope")
    (root / ".trw" / "config.yaml").write_text("framework_read_scope: full\n", encoding="utf-8")
    out = _run_session_start(root, "startup")
    assert "35,073" in out, "the full-scope directive must state the measured whole-document size"
    assert "EXECUTION MODEL SUMMARY" not in out, "full scope names the document, not a section list"


def test_the_stale_size_claim_is_gone_from_every_copy(hook_dir: Path) -> None:
    """FR07: the directive self-described as '~385 lines / ~8k tokens'.

    The file measures 393 lines and 35,073 characters. A cost figure that is
    remembered rather than measured is the defect; this pins the correction in
    the shipped hook so it cannot be reintroduced by a copy-paste.
    """
    hook = (hook_dir / "session-start.sh").read_text(encoding="utf-8")
    assert "~385 lines" not in hook
    assert "~8k tokens" not in hook
    assert "35,073" in hook
