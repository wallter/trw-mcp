"""lib-trw.sh ``_json_get``: the python3 fallback prints exactly what jq prints.

The hooks read JSON through one helper that uses jq when it is on PATH and
python3's json module otherwise. Each case below pins the expected stdout and
exit class as a literal, so the python3 half is checked on every host. The jq
half is checked against the same literals wherever jq is installed. The literals
were taken from jq 1.7.1 and 1.8.2, which agree on every case here.

With neither parser the helper returns 1 and prints nothing. The hooks then
degrade as they did before jq was optional (``jq_unavailable``,
``change_evidence_unknown``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._layout import HAS_JQ, PACKAGE_ROOT, path_without

pytestmark = pytest.mark.unit

_LIB = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"
_OK, _ERR = "ok", "error"

#: (id, stdin, args, expected stdout, exit class)
_CASES: list[tuple[str, str, list[str], str, str]] = [
    ("string", '{"a":"x"}', [".a"], "x\n", _OK),
    ("nested-escapes", '{"a":{"b":"q\\"b\\\\s\\n\\tu\\u00e9"}}', [".a.b"], 'q"b\\s\n\tué\n', _OK),
    ("first-null-falls-through", '{"a":null,"b":"y"}', [".a", ".b"], "y\n", _OK),
    ("false-falls-through", '{"a":false,"b":"y"}', [".a", ".b"], "y\n", _OK),
    ("empty-string-is-a-value", '{"a":"","b":"y"}', [".a", ".b"], "\n", _OK),
    ("missing-prints-nothing", '{"a":1}', [".b"], "", _OK),
    ("default", "{}", ["--default", "unknown", ".a"], "unknown\n", _OK),
    ("default-not-used", '{"a":"v"}', ["--default", "unknown", ".a"], "v\n", _OK),
    ("default-number-text", "{}", ["--default", "0", ".n"], "0\n", _OK),
    ("null-midway-uses-default", '{"n":null}', ["--default", "d", ".n.x"], "d\n", _OK),
    ("int", '{"i":12}', [".i"], "12\n", _OK),
    ("negative-and-big-int", '{"i":-100000000000000000001}', [".i"], "-100000000000000000001\n", _OK),
    ("bool", '{"t":true}', [".t"], "true\n", _OK),
    ("simple-floats", '{"a":1.5,"b":0.1,"c":1.0}', [".a"], "1.5\n", _OK),
    ("float-point-one", '{"b":0.1}', [".b"], "0.1\n", _OK),
    ("float-one-point-zero", '{"c":1.0}', [".c"], "1.0\n", _OK),
    (
        "object-pretty",
        '{"o":{"k":[1,{"z":null}],"u":"\\u00e9","e":{},"l":[]}}',
        [".o"],
        '{\n  "k": [\n    1,\n    {\n      "z": null\n    }\n  ],\n  "u": "é",\n  "e": {},\n  "l": []\n}\n',
        _OK,
    ),
    ("empty-object", '{"o":{}}', [".o"], "{}\n", _OK),
    ("strings-only-drops-number", '{"a":5}', ["--strings", ".a"], "", _OK),
    ("strings-only-keeps-string", '{"a":"s"}', ["--strings", ".a"], "s\n", _OK),
    ("arg-key", '{"s.1\\"x":{"run_path":"/r"}}', ["--arg", 's.1"x', ".$arg.run_path"], "/r\n", _OK),
    ("arg-key-missing", '{"s":{"run_path":"/r"}}', ["--arg", "t", ".$arg.run_path"], "", _OK),
    ("lone-low-surrogate", '{"s":"\\udc00x"}', [".s"], "\ufffdx\n", _OK),
    ("lone-high-surrogate-is-an-error", '{"s":"\\ud800x"}', [".s"], "", _ERR),
    ("lone-high-anywhere-is-an-error", '{"s":"ok","k\\ud800":1}', [".s"], "", _ERR),
    ("surrogate-pair", '{"s":"\\ud83d\\ude00"}', [".s"], "\U0001f600\n", _OK),
    ("through-array-is-an-error", '{"a":[1],"b":"x"}', [".a.k", ".b"], "", _ERR),
    ("top-level-array-is-an-error", "[1]", [".a"], "", _ERR),
    ("invalid-json-is-an-error", "nope", [".a"], "", _ERR),
    ("empty-input", "", [".a"], "", _OK),
    ("whitespace-input", "  \n", [".a"], "", _OK),
    ("bad-path-refused", '{"a":1}', [".a[0]"], "", _ERR),
    ("relative-path-refused", '{"a":1}', ["a"], "", _ERR),
]


def _run(stdin: str, args: list[str], path: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["sh", "-c", '. "$TRW_LIB"; _json_get "$@"', "sh", *args],
        input=stdin.encode("utf-8"),
        capture_output=True,
        env={"PATH": path, "TRW_LIB": str(_LIB), "HOME": os.environ.get("HOME", "/tmp")},
        check=False,
    )


def _assert_case(result: subprocess.CompletedProcess[bytes], expected: str, exit_class: str) -> None:
    assert result.stdout.decode("utf-8") == expected
    assert (result.returncode == 0) is (exit_class == _OK), result.returncode


@pytest.fixture(scope="module")
def python_only_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = path_without(tmp_path_factory.mktemp("nojq"), {"jq"})
    assert shutil.which("jq", path=path) is None
    assert shutil.which("python3", path=path) is not None, "the fallback needs python3"
    return path


@pytest.mark.parametrize(
    ("stdin", "args", "expected", "exit_class"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES]
)
def test_python3_fallback_prints_what_jq_prints(
    python_only_path: str, stdin: str, args: list[str], expected: str, exit_class: str
) -> None:
    _assert_case(_run(stdin, args, python_only_path), expected, exit_class)


@pytest.mark.skipif(not HAS_JQ, reason="compares against jq itself; the python3 half above runs everywhere")
@pytest.mark.parametrize(
    ("stdin", "args", "expected", "exit_class"), [c[1:] for c in _CASES], ids=[c[0] for c in _CASES]
)
def test_jq_prints_the_pinned_output(stdin: str, args: list[str], expected: str, exit_class: str) -> None:
    _assert_case(_run(stdin, args, os.environ["PATH"]), expected, exit_class)


def test_the_file_form_reads_the_same_document(python_only_path: str, tmp_path: Path) -> None:
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"sess": {"run_path": "/runs/r1"}}), encoding="utf-8")
    args = ["--file", str(pins), "--arg", "sess", ".$arg.run_path"]

    _assert_case(_run("", args, python_only_path), "/runs/r1\n", _OK)
    if HAS_JQ:
        _assert_case(_run("", args, os.environ["PATH"]), "/runs/r1\n", _OK)
    _assert_case(_run("", ["--file", str(tmp_path / "absent.json"), ".a"], python_only_path), "", _ERR)


def test_with_no_parser_it_returns_1_and_prints_nothing(tmp_path: Path) -> None:
    path = path_without(tmp_path, {"jq", "python3"})
    assert shutil.which("python3", path=path) is None

    result = _run('{"a":"x"}', [".a"], path)

    assert result.stdout == b""
    assert result.returncode == 1
