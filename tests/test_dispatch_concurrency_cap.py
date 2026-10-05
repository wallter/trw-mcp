"""PRD-CORE-355: a per-user cap on concurrent dispatch children, and an opt-in required effort.

Children are fake bash stubs (``tests._dispatch_host``). Concurrency is measured
by the stubs themselves: each one registers in a shared ``live`` directory while
it runs and logs how many were live at once, so the bound is observed on real
processes rather than inferred from the lock code.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests._dispatch_host import install_stub, write_stub
from trw_mcp.dispatch import _runner, _slots
from trw_mcp.dispatch._error_class import _RETRYABLE
from trw_mcp.dispatch._policy import policy_record
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._slots import SLOT_DIR_ENV, SLOT_HELD_ENV, SlotSettings, SlotUnavailableError, dispatch_slot
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.models.config import DispatchConfig

#: The source tree pytest imports; a bare subprocess would import the venv's installed copy instead.
_SRC = str(Path(_slots.__file__).resolve().parents[2])
_CLIENT = "grok"  # not an OAuth client: no credential lock serializes the runs being counted


@pytest.fixture()
def slot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    where = tmp_path / "slots"
    monkeypatch.setenv(SLOT_DIR_ENV, str(where))
    monkeypatch.delenv(SLOT_HELD_ENV, raising=False)
    return where


def _set_cap(monkeypatch: pytest.MonkeyPatch, cap: int, wait_s: float = 30.0) -> None:
    settings = SlotSettings(cap=cap, wait_s=wait_s)
    monkeypatch.setattr("trw_mcp.dispatch._slots.slot_settings", lambda: settings)
    monkeypatch.setattr("trw_mcp.dispatch._policy.slot_settings", lambda: settings)


def _counting_body(tmp_path: Path, *, hold_s: float = 0.4) -> tuple[str, Path]:
    live, log = tmp_path / "live", tmp_path / "live.log"
    live.mkdir(exist_ok=True)
    body = (
        f'touch "{live}/$$"\n'
        f'ls "{live}" | wc -l | tr -d " " >> "{log}"\n'
        f"sleep {hold_s}\n"
        f'rm -f "{live}/$$"\n'
        """echo '{"result": "ok"}'\n"""
    )
    return body, log


def _max_live(log: Path) -> int:
    return max(int(line) for line in log.read_text().split())


def _slot_is_free(where: Path, cap: int = 1) -> bool:
    try:
        with dispatch_slot(cap, wait_s=0, nested=True, directory=where):
            return True
    except SlotUnavailableError:  # trw-fail-silent-allow: test probe; a held slot is the answer False
        return False


@pytest.fixture()
def held(slot_dir: Path) -> Iterator[Callable[[int], None]]:
    """Hold slots from a separate thread, as another dispatch would; released at teardown."""
    stop = threading.Event()
    threads: list[threading.Thread] = []

    def hold(cap: int) -> None:
        ready = threading.Event()

        def run() -> None:
            with dispatch_slot(cap, wait_s=5, directory=slot_dir):
                ready.set()
                stop.wait(30)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert ready.wait(5)
        threads.append(thread)

    yield hold
    stop.set()
    for thread in threads:
        thread.join(5)


# ── FR01 / NFR01: off by default, and off means untouched ─────────────────────


def test_cap_zero_touches_no_slot_file_and_exports_no_marker(
    tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 0)
    install_stub(tmp_path, monkeypatch, """env; echo '{"result": "ok"}'\n""")

    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    assert result.exit_code == 0
    assert not slot_dir.exists()
    assert SLOT_HELD_ENV not in result.raw_stdout and SLOT_DIR_ENV not in result.raw_stdout
    assert "slot" not in policy_record(DispatchRequest(client=_CLIENT, prompt="p"))


def test_the_shipped_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._slots import slot_settings
    from trw_mcp.models.config import TRWConfig

    shipped = TRWConfig()  # defaults only: a machine config enabling the cap must not leak in
    cfg = shipped.dispatch
    assert (cfg.dispatch_max_concurrent_children, cfg.dispatch_require_effort) == (0, False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: shipped)
    assert slot_settings().cap == 0


# ── FR02: the bound holds across threads and across OS processes ─────────────


def test_cap_bounds_live_children(tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_cap(monkeypatch, 2)
    body, log = _counting_body(tmp_path)
    install_stub(tmp_path, monkeypatch, body)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _i: _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p")), range(5)))

    assert all(r.exit_code == 0 for r in results)
    assert len(log.read_text().split()) == 5
    assert _max_live(log) == 2
    assert sorted(p.name for p in slot_dir.iterdir()) == ["slot-0.lock", "slot-1.lock"]
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in slot_dir.iterdir())


def _process_worker(stub: str, slot_dir: str, cap: int, out: str) -> None:
    """One OS process running one capped dispatch (spawned; module-level so it pickles)."""
    os.environ[SLOT_DIR_ENV] = slot_dir
    os.environ.pop(SLOT_HELD_ENV, None)
    from trw_mcp.dispatch import _runner as runner

    _slots.slot_settings = lambda: SlotSettings(cap=cap, wait_s=60)  # type: ignore[assignment]
    runner.build_command = lambda _req, *, confined=False: [stub]  # type: ignore[assignment]
    runner._needs_host_confinement = lambda _req: False  # type: ignore[assignment]
    result = runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))
    Path(out).write_text(str(result.exit_code))


@pytest.mark.parametrize("caps", [(2, 2, 2, 2), (2, 1, 2, 1)], ids=["same-cap", "mixed-caps-largest-wins"])
def test_separate_processes_share_the_bound(tmp_path: Path, slot_dir: Path, caps: tuple[int, ...]) -> None:
    body, log = _counting_body(tmp_path, hold_s=1.0)
    stub = write_stub(tmp_path, "stub-cli", body)
    ctx = multiprocessing.get_context("spawn")
    outs = [tmp_path / f"out-{i}" for i in range(len(caps))]
    procs = [
        ctx.Process(target=_process_worker, args=(str(stub), str(slot_dir), cap, str(out)))
        for cap, out in zip(caps, outs, strict=True)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(120)

    assert [out.read_text() for out in outs] == ["0"] * len(caps)
    assert _max_live(log) <= max(caps)


# ── FR03: a bounded wait ends in a named, non-retryable refusal ──────────────


def test_wait_timeout_is_named_refusal(
    tmp_path: Path, slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 1, wait_s=0.6)
    install_stub(tmp_path, monkeypatch, "echo never\n")
    launches: list[object] = []
    monkeypatch.setattr(_runner.subprocess, "Popen", lambda *a, **k: launches.append(a))  # type: ignore[arg-type]
    held(1)

    started = time.monotonic()
    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))
    elapsed = time.monotonic() - started

    assert launches == []
    assert (result.ok, result.silence_reason, result.error_class) == (False, "concurrency_cap", "concurrency_cap")
    assert "dispatch_max_concurrent_children" in result.raw_stderr
    assert "dispatch_slot_wait_s" in result.raw_stderr
    assert 0.5 <= elapsed < 3, "waited the bound once and was not retried"
    assert "concurrency_cap" not in _RETRYABLE


def test_a_synchronous_mcp_caller_gets_a_short_slot_wait(
    slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import dispatch as tool

    _set_cap(monkeypatch, 1, wait_s=600)
    held(1)
    monkeypatch.setattr(tool, "_SYNC_SLOT_WAIT_S", 0.5)

    started = time.monotonic()
    result = tool.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    assert result.silence_reason == "concurrency_cap"
    assert time.monotonic() - started < 3, "the MCP thread must not wait the configured 600s"


def test_explicit_slot_wait_never_raises_the_configured_wait(
    slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 1, wait_s=0.5)
    held(1)

    started = time.monotonic()
    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"), slot_wait_s=600)

    assert result.silence_reason == "concurrency_cap"
    assert time.monotonic() - started < 3


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions as a non-root user")
def test_an_unwritable_slot_dir_fails_open_and_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    locked = tmp_path / "ro"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv(SLOT_DIR_ENV, str(locked / "slots"))
    monkeypatch.delenv(SLOT_HELD_ENV, raising=False)
    _set_cap(monkeypatch, 1)
    recorded: list[dict[str, object]] = []
    monkeypatch.setattr("trw_mcp.dispatch._usage.record_slot_outcome", lambda _c, slot: recorded.append(slot) or True)
    install_stub(tmp_path, monkeypatch, """echo '{"result": "ok"}'\n""")
    try:
        result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))
    finally:
        locked.chmod(0o700)

    assert result.exit_code == 0
    assert [r["outcome"] for r in recorded] == ["unavailable"]


def test_a_background_job_waits_at_most_half_its_timeout(
    slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 1, wait_s=600)
    held(1)

    started = time.monotonic()
    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p", timeout_s=2), pid_callback=lambda _pid: None)

    assert result.silence_reason == "concurrency_cap"
    assert time.monotonic() - started < 3, "the job watchdog budgets prelaunch + 1.5x timeout"


# ── FR04: a nested dispatch never waits ──────────────────────────────────────


def test_nested_dispatch_never_waits(
    tmp_path: Path, slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 1, wait_s=600)
    install_stub(tmp_path, monkeypatch, "echo never\n")
    held(1)  # the parent's slot
    monkeypatch.setenv(SLOT_HELD_ENV, "1")

    started = time.monotonic()
    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    assert result.silence_reason == "concurrency_cap"
    assert time.monotonic() - started < 1
    assert "does not wait" in result.raw_stderr


def test_nested_dispatch_with_a_free_slot_runs(
    tmp_path: Path, slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 2, wait_s=600)
    install_stub(tmp_path, monkeypatch, """echo '{"result": "ok"}'\n""")
    held(1)
    monkeypatch.setenv(SLOT_HELD_ENV, "1")

    assert _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p")).exit_code == 0


def test_a_slot_holder_marks_its_child_and_pins_the_slot_dir(
    tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cap(monkeypatch, 1)
    install_stub(tmp_path, monkeypatch, "env\n")

    out = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p")).raw_stdout

    assert f"{SLOT_HELD_ENV}=1" in out.splitlines()
    assert f"{SLOT_DIR_ENV}={slot_dir}" in out.splitlines()


def test_a_real_nested_dispatch_at_cap_one_refuses_instead_of_deadlocking(
    tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child of a slot holder runs its own dispatch: cap 1 is taken by its parent, so it must refuse."""
    _set_cap(monkeypatch, 1, wait_s=600)
    inner = tmp_path / "inner.py"
    inner.write_text(
        f"import sys; sys.path.insert(0, {_SRC!r})\n"
        "from trw_mcp.dispatch import _runner, _slots\n"
        "from trw_mcp.dispatch._types import DispatchRequest\n"
        "_slots.slot_settings = lambda: _slots.SlotSettings(cap=1, wait_s=600)\n"
        f"print(_runner.dispatch(DispatchRequest(client={_CLIENT!r}, prompt='inner')).silence_reason)\n"
    )
    install_stub(tmp_path, monkeypatch, f'"{sys.executable}" "{inner}"\n')

    started = time.monotonic()
    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="outer", timeout_s=120))

    assert result.exit_code == 0, result.raw_stderr
    assert result.raw_stdout.strip().splitlines()[-1] == "concurrency_cap"
    assert time.monotonic() - started < 60


# ── FR05: release on every path; lock order; no inherited descriptor ───────


@pytest.mark.parametrize(
    ("body", "timeout_s"),
    [
        ("""echo '{"result": "ok"}'\n""", 30),
        ("echo boom >&2; exit 3\n", 30),
        ("sleep 30\n", 1),
    ],
    ids=["success", "failure", "timeout"],
)
def test_slot_release_paths(
    tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch, body: str, timeout_s: int
) -> None:
    _set_cap(monkeypatch, 1)
    install_stub(tmp_path, monkeypatch, body)

    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p", timeout_s=timeout_s))

    assert result.silence_reason != "concurrency_cap"
    assert _slot_is_free(slot_dir)


def test_slot_released_when_the_run_raises(slot_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_cap(monkeypatch, 1)

    def boom(*_a: object, **_k: object) -> object:
        assert not _slot_is_free(slot_dir), "the slot is held while the run is in flight"
        raise RuntimeError("unexpected")

    monkeypatch.setattr(_runner, "run_dispatch", boom)
    with pytest.raises(RuntimeError):
        _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))
    assert _slot_is_free(slot_dir)


def test_slot_released_on_process_death(slot_dir: Path) -> None:
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from trw_mcp.dispatch._slots import dispatch_slot\n"
            f"with dispatch_slot(1, wait_s=0, directory=Path({str(slot_dir)!r})):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        assert not _slot_is_free(slot_dir)
        holder.kill()
        holder.wait(10)
        assert _slot_is_free(slot_dir)
    finally:
        if holder.poll() is None:
            holder.kill()
        if holder.stdout is not None:
            holder.stdout.close()


def test_child_does_not_inherit_the_slot_fd(tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The child must hold no descriptor on the slot file: one that outlived a dead owner would keep the slot."""
    _set_cap(monkeypatch, 1)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, sys\n"
        "target = os.stat(sys.argv[1]).st_ino\n"
        "leaked = []\n"
        "for fd in range(3, 256):\n"
        "    try:\n"
        "        if os.fstat(fd).st_ino == target:\n"
        "            leaked.append(fd)\n"
        "    except OSError:\n"
        "        pass\n"
        "print('leaked=' + ','.join(map(str, leaked)))\n"
    )
    install_stub(tmp_path, monkeypatch, f'"{sys.executable}" "{probe}" "{slot_dir}/slot-0.lock"\n')

    result = _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    assert result.exit_code == 0, result.raw_stderr
    assert "leaked=" in result.raw_stdout and result.raw_stdout.strip().splitlines()[-1] == "leaked="
    assert _slot_is_free(slot_dir)


def test_slot_is_taken_before_the_credential_lock(
    tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.dispatch import _error_class

    _set_cap(monkeypatch, 1)
    install_stub(tmp_path, monkeypatch, """echo '{"result": "ok"}'\n""")
    seen: list[bool] = []
    original = _error_class.run_guarded

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(bool(_slots.held_slot_env()) and not _slot_is_free(slot_dir))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_error_class, "run_guarded", spy)
    _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    assert seen == [True]


# ── FR06: required effort (opt-in) ───────────────────────────────────────────


def _cfg(**fields: object) -> DispatchConfig:
    return DispatchConfig(**fields, operator_set=frozenset(fields))  # type: ignore[arg-type]


def _resolve(client: str, cfg: DispatchConfig, **kw: object) -> DispatchRequest:
    args: dict[str, object] = {
        "client": client,
        "prompt": "p",
        "role": None,
        "model": None,
        "cwd": None,
        "timeout_s": None,
        "isolate": False,
        "use_pty": False,
        "dispatch_cfg": cfg,
    }
    args.update(kw)
    return resolve_dispatch_request(**args)  # type: ignore[arg-type]


def _carrier_and_carrierless() -> tuple[str, str]:
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    # A carrier with no client default effort: a spec default (e.g. codex "low") is a
    # deliberate TRW choice, so require_effort correctly never refuses it.
    carriers = [
        cid
        for cid, s in CLIENT_SPECS.items()
        if (s.effort_flag or s.effort_config_key) and getattr(s, "default_effort", None) is None
    ]
    without = [cid for cid, s in CLIENT_SPECS.items() if not (s.effort_flag or s.effort_config_key)]
    return ("claude" if "claude" in carriers else carriers[0]), without[0]


def test_require_effort() -> None:
    carrier, carrierless = _carrier_and_carrierless()
    on = _cfg(dispatch_require_effort=True)

    with pytest.raises(DispatchResolutionError) as refused:
        _resolve(carrier, on)
    assert refused.value.exit_code == 2
    assert "--effort" in str(refused.value) and "dispatch_default_effort" in str(refused.value)

    assert _resolve(carrier, on, effort="high").effort == "high"
    assert _resolve(carrier, _cfg(dispatch_require_effort=True, dispatch_default_effort="low")).effort == "low"
    assert _resolve(carrier, _cfg()).effort is None, "off by default: no refusal"
    assert _resolve(carrierless, on).effort is None, "a client with no effort carrier is not refused"


def test_require_effort_accepts_a_client_default_effort() -> None:
    """A client spec default (source "default") is an explicit TRW choice, never refused."""
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    defaulted = [cid for cid, s in CLIENT_SPECS.items() if getattr(s, "default_effort", None)]
    assert defaulted, "no client carries a default effort; this test needs one to be meaningful"
    req = _resolve(defaulted[0], _cfg(dispatch_require_effort=True))
    assert req.effort == CLIENT_SPECS[defaulted[0]].default_effort


def test_require_effort_spares_a_haiku_model() -> None:
    from trw_mcp.dispatch._policy import require_effort

    assert require_effort("claude", "claude-haiku-4-5", "none") is None
    assert require_effort("claude", "claude-sonnet-4-5", "none") is not None
    assert require_effort("claude", None, "table") is None


# ── FR07: recorded outcomes ──────────────────────────────────────────────────


def test_cap_is_recorded(tmp_path: Path, slot_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_cap(monkeypatch, 2, wait_s=45)
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.dispatch._usage._active_run", lambda: run)
    install_stub(tmp_path, monkeypatch, """echo '{"result": "ok"}'\n""")

    assert policy_record(DispatchRequest(client=_CLIENT, prompt="p"))["slot"] == {"cap": 2, "wait_limit_s": 45}
    _runner.dispatch(DispatchRequest(client=_CLIENT, prompt="p"))

    lines = [line for f in sorted((run / "meta").glob("events-*.jsonl")) for line in f.read_text().splitlines()]
    (event,) = [r for r in map(json.loads, lines) if r.get("event_type") == "dispatch_policy"]
    slot = event["payload"]["slot"]
    assert (slot["outcome"], slot["index"], slot["cap"], slot["nested"]) == ("acquired", 0, 2, False)
    assert isinstance(slot["wait_s"], float)


def test_capped_fanout_lane_reports_concurrency_cap(
    slot_dir: Path, held: Callable[[int], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._dispatch_fanout import _run_fanout

    _set_cap(monkeypatch, 1, wait_s=0.3)
    held(1)

    (lane,) = _run_fanout([("grok-lane", DispatchRequest(client=_CLIENT, prompt="p"))])

    assert (lane["target"], lane["ok"], lane["reason"]) == ("grok-lane", False, "concurrency_cap")


def test_runner_env_forwards_the_dispatch_cap_env() -> None:
    from trw_mcp.dispatch._env import build_runner_env

    names = ("TRW_DISPATCH_MAX_CONCURRENT_CHILDREN", "TRW_DISPATCH_SLOT_WAIT_S", "TRW_DISPATCH_REQUIRE_EFFORT")
    src = {
        "PATH": "/bin",
        "TRW_DISPATCH_MAX_CONCURRENT_CHILDREN": "3",
        "TRW_DISPATCH_SLOT_WAIT_S": "9",
        "TRW_DISPATCH_REQUIRE_EFFORT": "true",
        "TRW_UNRELATED": "x",
    }
    env = build_runner_env(_CLIENT, source_env=src)

    assert {n: env[n] for n in names} == {n: src[n] for n in names}
    assert "TRW_UNRELATED" not in env


def test_the_config_loader_reads_the_forwarded_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.config import TRWConfig

    monkeypatch.setenv("TRW_DISPATCH_MAX_CONCURRENT_CHILDREN", "3")
    monkeypatch.setenv("TRW_DISPATCH_SLOT_WAIT_S", "9")
    monkeypatch.setenv("TRW_DISPATCH_REQUIRE_EFFORT", "true")
    cfg = TRWConfig().dispatch
    assert (cfg.dispatch_max_concurrent_children, cfg.dispatch_slot_wait_s, cfg.dispatch_require_effort) == (
        3,
        9.0,
        True,
    )
