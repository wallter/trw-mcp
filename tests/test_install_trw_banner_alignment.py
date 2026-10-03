"""The Python installer's boxed banners keep every row at one display width, in colour and without it."""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import unicodedata
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _load(monkeypatch: pytest.MonkeyPatch, *, color: bool) -> ModuleType:
    """Import the template with colour forced on or off (the gate reads isatty and NO_COLOR at import)."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    if not color:
        monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    spec = importlib.util.spec_from_file_location("install_trw_banner_under_test", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture(fn: object, *args: object, **kwargs: object) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)  # type: ignore[operator]
    return buf.getvalue()


def _box_rows(output: str) -> list[str]:
    rows = [_ANSI.sub("", ln) for ln in output.splitlines()]
    return [r for r in rows if r.startswith(("╭", "│", "╰"))]


def _assert_one_width(output: str) -> None:
    rows = _box_rows(output)
    assert len(rows) >= 3, output
    assert all(unicodedata.east_asian_width(c) not in "WF" for r in rows for c in r)
    assert len({len(r) for r in rows}) == 1, "\n".join(rows)
    assert rows[0].endswith("╮") and rows[-1].endswith("╯")
    assert all(r.endswith("│") for r in rows[1:-1])


@pytest.mark.parametrize("color", [True, False], ids=["color", "no-color"])
@pytest.mark.parametrize("health_ok", [True, False], ids=["healthy", "doctor-failed"])
@pytest.mark.parametrize("version", ["8.1.2", "8.1.0.dev25"])
def test_success_box_rows_line_up(monkeypatch: pytest.MonkeyPatch, color: bool, health_ok: bool, version: str) -> None:
    mod = _load(monkeypatch, color=color)
    monkeypatch.setattr(mod, "_MCP_EFFECTIVE_VERSION", version)
    ui = mod.UI(interactive=True)
    out = _capture(mod.show_success_banner, ui, "connected", [], health_ok=health_ok)
    _assert_one_width(out)
    assert ("\x1b[" in out) is color
    assert ("is ready" in out) is health_ok
    assert ("health check FAILED" in out) is (not health_ok)


@pytest.mark.parametrize("color", [True, False], ids=["color", "no-color"])
def test_start_banner_rows_line_up_and_carry_the_current_tagline(monkeypatch: pytest.MonkeyPatch, color: bool) -> None:
    mod = _load(monkeypatch, color=color)
    out = _capture(mod.show_banner, mod.UI(interactive=True))
    _assert_one_width(out)
    assert "The engineering operating layer for AI agents" in out
    assert "BSL-1.1" in out and "/license" in out


def test_tips_name_only_real_export_flags() -> None:
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert "trw-mcp export . --scope learnings --format csv" in text
    assert "export . learnings" not in text
