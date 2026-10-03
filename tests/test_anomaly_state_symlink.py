"""The anomaly detector never writes through a symlink a checkout planted under ``.trw/security`` (CORE-337-D).

Its argument-hash baseline appended with ``path.open("a")`` and rolled through a fixed ``<name>.tmp``, and its
shadow clock was guarded only at the leaf, on every tool call of every non-reviewer server. A cloned repository
that ships ``.trw/security/mcp_arg_baseline.jsonl`` (or ``.trw/security`` itself, or the ``.tmp`` name) as a
symlink turned that into "append to, or replace, a file outside the project". The writes now go through the
checkout adapter beneath the project root, which refuses any symlinked component; detection carries on in memory.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="needs POSIX no-follow opens")


def _detector(project: Path, **limits: int):  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig

    security = project / ".trw" / "security"
    root = {"checkout_root": project} if "checkout_root" in AnomalyDetectorConfig.model_fields else {}
    config = AnomalyDetectorConfig(
        **root,
        shadow_clock_path=security / "mcp_shadow_start.yaml",
        baseline_store_path=security / "mcp_arg_baseline.jsonl",
        **limits,
    )
    return AnomalyDetector(config=config, run_dir=None, fallback_dir=project / ".trw" / "audit")


def _observe(detector, args_hash: str) -> list[str]:  # type: ignore[no-untyped-def]
    from trw_mcp.security.anomaly_detector import AnomalyObservation

    obs = AnomalyObservation(ts=datetime.now(tz=timezone.utc), server="trw", tool="trw_recall", args_hash=args_hash)
    fired: list[str] = detector.observe(obs)
    return fired


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    victim = tmp_path / "outside"
    victim.mkdir()
    (victim / "dotfile").write_text("export KEEP=1\n", encoding="utf-8")
    return victim


def test_a_planted_baseline_symlink_is_never_appended_through(tmp_path: Path, outside: Path) -> None:
    project = tmp_path / "proj"
    (project / ".trw" / "security").mkdir(parents=True)
    (project / ".trw" / "security" / "mcp_arg_baseline.jsonl").symlink_to(outside / "dotfile")

    fired = _observe(_detector(project), "h1")

    assert (outside / "dotfile").read_text(encoding="utf-8") == "export KEEP=1\n"
    assert "novel_arg_pattern" in fired, "detection carries on in memory"


def test_a_symlinked_security_dir_receives_no_state(tmp_path: Path, outside: Path) -> None:
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "security").symlink_to(outside, target_is_directory=True)

    _observe(_detector(project), "h1")

    assert sorted(p.name for p in outside.iterdir()) == ["dotfile"], "no shadow clock or baseline outside"


def test_a_planted_roll_tmp_symlink_is_never_written_through(tmp_path: Path, outside: Path) -> None:
    project = tmp_path / "proj"
    security = project / ".trw" / "security"
    security.mkdir(parents=True)
    detector = _detector(project, max_baseline_store_lines=2)
    for i in range(2):
        _observe(detector, f"h{i}")
    (security / "mcp_arg_baseline.jsonl.tmp").symlink_to(outside / "dotfile")

    _observe(detector, "h-roll")  # the third line rolls the store

    assert (outside / "dotfile").read_text(encoding="utf-8") == "export KEEP=1\n"
    assert len((security / "mcp_arg_baseline.jsonl").read_text(encoding="utf-8").splitlines()) <= 2


def test_without_no_follow_file_support_the_state_stays_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Luna r1 BLOCKER: the by-path Windows fallback is not race-safe, so persistence fails closed there."""
    from structlog.testing import capture_logs
    from trw_memory import safe_fs

    monkeypatch.setattr(safe_fs, "anchored_removal_supported", lambda: False)
    project = tmp_path / "proj"
    project.mkdir()

    with capture_logs() as logs:
        detector = _detector(project)
        first = _observe(detector, "h1")
        repeat = _observe(detector, "h1")

    assert not (project / ".trw" / "security").exists(), "no state is written by path (audit events are separate)"
    assert "novel_arg_pattern" in first and "novel_arg_pattern" not in repeat, "detection runs in memory"
    assert [e for e in logs if e["event"] == "mcp_anomaly_state_in_memory"]


def test_a_baseline_planted_as_a_symlink_is_never_loaded(tmp_path: Path, outside: Path) -> None:
    """Luna r1 MAJOR: the startup load read the store by name, following a link to an attacker's baseline."""
    import json

    (outside / "baseline.jsonl").write_text(
        json.dumps({"type": "arg_baseline", "server": "trw", "tool": "trw_recall", "arg_hash": "hX"}) + "\n",
        encoding="utf-8",
    )
    project = tmp_path / "proj"
    (project / ".trw" / "security").mkdir(parents=True)
    (project / ".trw" / "security" / "mcp_arg_baseline.jsonl").symlink_to(outside / "baseline.jsonl")

    fired = _observe(_detector(project), "hX")

    assert "novel_arg_pattern" in fired, "the linked baseline was not loaded"


def test_a_planted_clock_leaf_symlink_is_never_written_through(tmp_path: Path, outside: Path) -> None:
    project = tmp_path / "proj"
    (project / ".trw" / "security").mkdir(parents=True)
    (project / ".trw" / "security" / "mcp_shadow_start.yaml").symlink_to(outside / "dotfile")

    _observe(_detector(project), "h1")

    assert (outside / "dotfile").read_text(encoding="utf-8") == "export KEEP=1\n"


def test_a_symlinked_checkout_root_receives_no_state(tmp_path: Path, outside: Path) -> None:
    link = tmp_path / "proj-link"
    link.symlink_to(outside, target_is_directory=True)

    fired = _observe(_detector(link), "h1")

    assert not (outside / ".trw" / "security").exists(), "no shadow clock or baseline through the linked root"
    assert "novel_arg_pattern" in fired


def test_read_state_file_refuses_links_and_non_regular_files(tmp_path: Path, outside: Path) -> None:
    from trw_mcp.security._anomaly_state import read_state_file

    root = tmp_path / "proj"
    security = root / ".trw" / "security"
    security.mkdir(parents=True)
    (security / "real").write_text("ok\n", encoding="utf-8")
    (security / "link").symlink_to(outside / "dotfile")
    os.mkfifo(security / "fifo")
    (root / ".trw" / "linked").symlink_to(outside, target_is_directory=True)

    assert read_state_file(root, security / "real") == "ok\n"
    assert read_state_file(root, security / "absent") is None
    with pytest.raises(OSError):
        read_state_file(root, security / "link")
    with pytest.raises(OSError):
        read_state_file(root, security / "fifo")  # never blocks: opened non-blocking, then refused
    with pytest.raises(OSError):  # a symlinked directory is refused (O_NOFOLLOW), never walked through
        read_state_file(root, root / ".trw" / "linked" / "dotfile")
