"""Characterization: with ``hint_sidecar_ancestor_enabled`` off, the pre-edit hint is unchanged.

Pinned BEFORE the ancestor-sidecar read path landed, against the code as it
was: every case below records the full ``BeforeEditHintResult`` dump, the
``hint_delivered`` telemetry the call emitted, and the T2 text the edit hooks
render from it. The flag is switched off through the environment, which the
config accepted (and ignored) before the field existed, so this file is
byte-for-byte the same test on both sides of the change.

The ``missing`` case deliberately leaves an ANCESTOR batch sidecar in the
cache: with the flag off it must still read as ``sidecar_missing``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.channels.claude_code._hook_helpers import format_t2_hint
from trw_mcp.models.config import reload_config
from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools import _before_edit_hint_core
from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint

_SCHEMA = "risk-report-sidecar/v0"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _repo_with_two_commits(repo: Path) -> tuple[str, str]:
    """Return (first_sha, head_sha); the first commit is an ancestor of HEAD."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "foo.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "bar.py").write_text("y = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "two")
    return first, _git(repo, "rev-parse", "HEAD")


def _entitle(repo: Path) -> None:
    trw = repo / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier="pro", issued_to="t@t", expires_at=future)
    (trw / "entitlements.yaml").write_text(f"tier: pro\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n")


def _hint_entry(target: str) -> dict[str, Any]:
    return {
        "target_path": target,
        "target_exists_in_map": True,
        "importers": ["bar.py"],
        "inferred_tests": ["tests/test_foo.py"],
        "doc_references": ["docs/foo.md"],
        "co_change_neighbors": ["bar.py", "baz.py"],
        "hotspot_warnings": ["non-trivial fan-in (1 importers)"],
        "risk_score": 0.42,
    }


def _envelope(sha: str, payload: Any) -> str:
    return json.dumps({"schema_version": _SCHEMA, "sha": sha, "generated_at_unix": 1714000000.0, "payload": payload})


def _cache(repo: Path) -> Path:
    cache = repo / ".trw" / "distill" / "map-cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _setup(case: str, repo: Path) -> tuple[str, str, str]:
    """Build *case*; return (file_path to hint, first_sha, head_sha)."""
    first, head = _repo_with_two_commits(repo)
    _entitle(repo)
    cache = _cache(repo)
    target = "foo.py"
    if case == "exact_single":
        (cache / f"before-edit-hint-{head}.json").write_text(_envelope(head, _hint_entry("foo.py")))
    elif case == "exact_batch":
        (cache / f"before-edit-batch-{head}.json").write_text(_envelope(head, {"hints": [_hint_entry("foo.py")]}))
    elif case == "exact_batch_absolute_path":
        (cache / f"before-edit-batch-{head}.json").write_text(_envelope(head, {"hints": [_hint_entry("foo.py")]}))
        target = str(repo / "foo.py")
    elif case == "missing_with_ancestor":
        (cache / f"before-edit-batch-{first}.json").write_text(_envelope(first, {"hints": [_hint_entry("foo.py")]}))
    elif case == "stale_sha":
        (cache / f"before-edit-hint-{head}.json").write_text(_envelope("0" * 40, _hint_entry("foo.py")))
    elif case == "malformed_json":
        (cache / f"before-edit-hint-{head}.json").write_text("{ not json")
    elif case == "batch_payload_missing":
        (cache / f"before-edit-batch-{head}.json").write_text(
            json.dumps({"schema_version": _SCHEMA, "sha": head, "generated_at_unix": 1714000000.0})
        )
    elif case == "malformed_batch":
        (cache / f"before-edit-batch-{head}.json").write_text(_envelope(head, {"hints": "nope"}))
    else:  # pragma: no cover - guards the parametrize list below
        raise AssertionError(case)
    return target, first, head


_EXACT_T2_TEXT = (
    "[TRW Distill Hint — T2]\n"
    "  RISK: 0.42\n"
    "  WARN: non-trivial fan-in (1 importers)\n"
    "  CO-CHANGE: bar.py, baz.py\n"
    "  TESTS: tests/test_foo.py"
)
_HINT_DUMP = {
    "target_path": "foo.py",
    "target_exists_in_map": True,
    "importers": ["bar.py"],
    "inferred_tests": ["tests/test_foo.py"],
    "doc_references": ["docs/foo.md"],
    "co_change_neighbors": ["bar.py", "baz.py"],
    "hotspot_warnings": ["non-trivial fan-in (1 importers)"],
    "risk_score": 0.42,
    "lessons": [],
    "lessons_status": None,
}
_BATCH_MISS = (
    "Batch sidecar does not cover {target!r} (it covers every file in the map at the commit it was"
    " built from, so this file is new since then or outside the map) — run: "
    "cd <repo> && trw-distill self-improve before-edit --repo . --file {target} --persist-sidecar"
)
_RUN = "Run: cd <repo> && trw-distill self-improve before-edit --repo . --file foo.py --persist-sidecar"

#: case -> (distill_status, distill_action, sidecar artifact, hint dump, T2 text)
_GOLDEN: dict[str, tuple[str, str | None, str | None, dict[str, Any] | None, str | None]] = {
    "exact_single": ("hint_available", None, "before-edit-hint-{head}.json", _HINT_DUMP, _EXACT_T2_TEXT),
    "exact_batch": ("hint_available", None, "before-edit-batch-{head}.json", _HINT_DUMP, _EXACT_T2_TEXT),
    "exact_batch_absolute_path": (
        "target_not_in_sidecar",
        _BATCH_MISS,
        "before-edit-batch-{head}.json",
        None,
        None,
    ),
    "missing_with_ancestor": ("sidecar_missing", _RUN, "before-edit-hint-{head}.json", None, None),
    "stale_sha": (
        "stale_sha",
        "Sidecar SHA='" + "0" * 40 + "'; HEAD={head} — re-run with --persist-sidecar",
        "before-edit-hint-{head}.json",
        None,
        None,
    ),
    "malformed_json": ("sidecar_missing", _RUN, "before-edit-hint-{head}.json", None, None),
    "malformed_batch": ("sidecar_malformed", _BATCH_MISS, "before-edit-batch-{head}.json", None, None),
    # Added after codex r1 (flag-off KI): pinned against the pre-change code, see the commit message.
    "batch_payload_missing": ("sidecar_missing", _RUN, "before-edit-hint-{head}.json", None, None),
}


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, str]]]:
    """Flag off via env, learnings port faked empty, telemetry captured."""
    monkeypatch.setenv("TRW_HINT_SIDECAR_ANCESTOR_ENABLED", "false")
    monkeypatch.setattr(_before_edit_hint_core, "_collect_learnings", lambda _fp, _root: ([], "ok"))
    emitted: list[dict[str, str]] = []

    def _capture(*, tier: str, distill_status: str, file_path: str, **extra: Any) -> None:
        emitted.append({"tier": tier, "distill_status": distill_status, "file_path": file_path, **extra})

    monkeypatch.setattr("trw_mcp.channels._distill_telemetry.emit_hint_delivered", _capture)
    reload_config()
    yield emitted
    reload_config()


def _render_like_the_hooks(result: _before_edit_hint_core.BeforeEditHintResult) -> str | None:
    """The T2 branch the claude_code/cursor/copilot hooks take, as it stood before the change."""
    hint = result.distill_hint
    if not (hint and result.distill_status == "hint_available"):
        return None
    return format_t2_hint(
        file_path=result.file_path,
        risk_score=hint.risk_score,
        hotspot_warnings=hint.hotspot_warnings,
        co_change_neighbors=hint.co_change_neighbors,
        inferred_tests=hint.inferred_tests,
        lessons=hint.lessons,
        lessons_status=hint.lessons_status,
    )


@pytest.mark.parametrize("case", sorted(_GOLDEN))
def test_flag_off_hint_is_byte_identical(case: str, tmp_path: Path, flag_off: list[dict[str, str]]) -> None:
    repo = tmp_path / "repo"
    target, _first, head = _setup(case, repo)
    status, action, artifact, hint_dump, text = _GOLDEN[case]
    cache = repo / ".trw" / "distill" / "map-cache"

    result = compute_before_edit_hint(file_path=target, repo_root=str(repo))

    expected_action = None if action is None else action.format(head=head, target=target)
    assert result.model_dump() == {
        "file_path": target,
        "tier": "pro",
        "distill_hint": hint_dump,
        "distill_status": status,
        "distill_action": expected_action,
        "distill_sidecar_path": str(cache / artifact.format(head=head)) if artifact else None,
        "distill_sidecar_sha": head,
        "learnings": [],
        "learnings_count": 0,
    }
    # Byte-level too: key order and JSON rendering are what trw_code returns.
    assert json.dumps(result.model_dump()) == json.dumps(
        {
            "file_path": target,
            "tier": "pro",
            "distill_hint": hint_dump,
            "distill_status": status,
            "distill_action": expected_action,
            "distill_sidecar_path": str(cache / artifact.format(head=head)) if artifact else None,
            "distill_sidecar_sha": head,
            "learnings": [],
            "learnings_count": 0,
        }
    )
    expected_tier = "T2" if status == "hint_available" else "pro"
    assert flag_off == [{"tier": expected_tier, "distill_status": status, "file_path": target}]
    rendered = _render_like_the_hooks(result)
    assert rendered == text


def test_batch_miss_text_matches_the_whole_map_batch_semantics() -> None:
    """E2E-INC-056: the batch sidecar is whole-map (trw-distill compute_whole_map_batch), so the miss text must
    not claim it covers only the files the last commit touched."""
    from trw_mcp.tools._before_edit_hint_core import _batch_miss_action

    text = _batch_miss_action("src/new_module.py")
    assert "last commit touched" not in text
    assert "every file in the map" in text and "new since then or outside the map" in text
    assert "--file src/new_module.py --persist-sidecar" in text
