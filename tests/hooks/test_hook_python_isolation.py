"""PRD-FIX-156 (sol r3): a hook's python3 never imports a module from the checkout.

``python3 -c`` and ``python3 -`` put the current directory first on ``sys.path``,
and every hook runs with the checkout as its working directory. A checkout that
ships ``json.py`` therefore ran its own code inside the JSON reader that stands
in for jq. ``-I`` (isolated mode) leaves the current directory, ``PYTHON*``
variables and the user site out. The census keeps every bundled python3 call
isolated; the behaviour test plants ``json.py`` and runs the real readers.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._layout import path_without
from tests.hooks._sh_census import DATA, census

pytestmark = pytest.mark.skipif(shutil.which("python3") is None, reason="python3 unavailable")

#: A python3 invocation that runs inline code or a script from stdin.
_PYTHON3 = re.compile(r"\bpython3\s+(?:-[A-Za-z]+\s+)*(?:-c\b|-\s)")
_ISOLATED = re.compile(r"\bpython3\s+(?:-[A-Za-z]*I[A-Za-z]*\s+)")


def test_census_every_bundled_python3_call_is_isolated() -> None:
    offenders = [
        f"{site.relpath}:{site.lineno}: {site.source}"
        for path in sorted(DATA.rglob("*.sh"))
        for site in census(path, _PYTHON3)
        if not _ISOLATED.search(site.source)
    ]
    assert offenders == [], f"python3 without -I imports from the checkout: {offenders}"


def test_census_catches_an_unisolated_call(tmp_path: Path) -> None:
    planted = tmp_path / "hook.sh"
    planted.write_text("#!/bin/sh\nprintf '{}' | python3 -c 'import json'\n", encoding="utf-8")
    assert [s.source for s in census(planted, _PYTHON3) if not _ISOLATED.search(s.source)]


_READERS = {
    "_json_get": ("hooks/lib-trw.sh", "_json_get .a"),
    "_json_string_leaves": ("hooks/lib-trw.sh", "_json_string_leaves a"),
    "cursor _read_json_field": ("hooks/cursor/lib-distill-hint.sh", "_read_json_field .a"),
    "copilot _read_json_field": ("copilot/hooks/lib-copilot-distill-hint.sh", "_read_json_field .a"),
}


@pytest.mark.parametrize("reader", sorted(_READERS))
def test_a_checkout_json_py_is_never_imported(tmp_path: Path, reader: str) -> None:
    lib, call = _READERS[reader]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    ran = tmp_path / "checkout-json-py-ran"
    (checkout / "json.py").write_text(f"open({str(ran)!r}, 'w').close()\nfrom _json import *\n", encoding="utf-8")

    result = subprocess.run(
        ["sh", "-c", f'. "{DATA / lib}"; {call}'],
        input='{"a":"x"}',
        capture_output=True,
        text=True,
        cwd=checkout,
        env={"PATH": path_without(tmp_path, {"jq"}), "HOME": os.environ.get("HOME", "/tmp")},
        timeout=30,
        check=False,
    )

    assert not ran.exists(), f"{reader} imported the checkout's json.py"
    assert result.stdout == "x\n"
