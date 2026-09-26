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


# --- _json_object (PRD-FIX-154 FR01): the write-side counterpart. jq -cn when
# jq is present; a shell builder using _json_escape otherwise. Neither parser
# is required on the shell path.

#: (id, args, expected parsed object, exit class)
_OBJECT_CASES: list[tuple[str, list[str], dict[str, object] | None, str]] = [
    ("plain-string", ["--str", "a", "plain"], {"a": "plain"}, _OK),
    ("double-quote", ["--str", "a", 'has"quote'], {"a": 'has"quote'}, _OK),
    ("backslash", ["--str", "a", "back\\slash"], {"a": "back\\slash"}, _OK),
    ("newline", ["--str", "a", "line1\nline2"], {"a": "line1\nline2"}, _OK),
    ("tab", ["--str", "a", "a\tb"], {"a": "a\tb"}, _OK),
    ("non-ascii", ["--str", "a", "café é"], {"a": "café é"}, _OK),
    ("quote-then-new-key", ["--str", "a", '","injected":"x'], {"a": '","injected":"x'}, _OK),
    ("empty-string", ["--str", "a", ""], {"a": ""}, _OK),
    ("int-plain", ["--int", "n", "42"], {"n": 42}, _OK),
    ("int-negative", ["--int", "n", "-7"], {"n": -7}, _OK),
    ("int-zero", ["--int", "n", "0"], {"n": 0}, _OK),
    ("int-non-numeric-coerced-to-zero", ["--int", "n", "abc"], {"n": 0}, _OK),
    ("int-empty-coerced-to-zero", ["--int", "n", ""], {"n": 0}, _OK),
    ("int-double-minus-coerced-to-zero", ["--int", "n", "--5"], {"n": 0}, _OK),
    (
        "multi-field",
        ["--str", "run_path", '/r/"q"\\p', "--int", "active_tasks", "3", "--str", "phase", "implement"],
        {"run_path": '/r/"q"\\p', "active_tasks": 3, "phase": "implement"},
        _OK,
    ),
    ("no-args-is-empty-object", [], {}, _OK),
    ("invalid-key-with-space", ["--str", "bad key", "v"], None, _ERR),
    ("invalid-key-empty", ["--str", "", "v"], None, _ERR),
    ("unknown-flag", ["--float", "a", "1"], None, _ERR),
]

#: A C0 control character (other than tab/newline) is dropped by _json_escape;
#: this is the one documented parity gap between jq and the shell path.
_OBJECT_CONTROL_CHAR_ID = "control-char-dropped-on-shell-path"


def _run_object(args: list[str], path: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["sh", "-c", '. "$TRW_LIB"; _json_object "$@"', "sh", *args],
        capture_output=True,
        env={"PATH": path, "TRW_LIB": str(_LIB), "HOME": os.environ.get("HOME", "/tmp")},
        check=False,
    )


def _assert_object_case(
    result: subprocess.CompletedProcess[bytes], expected: dict[str, object] | None, exit_class: str
) -> None:
    if exit_class == _ERR:
        assert result.returncode != 0, result.stdout
        assert result.stdout == b""
        return
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode("utf-8")
    assert out.endswith("\n")
    assert "\n" not in out[:-1], "must be one line"
    assert json.loads(out) == expected


@pytest.mark.parametrize(
    ("case_id", "args", "expected", "exit_class"), _OBJECT_CASES, ids=[c[0] for c in _OBJECT_CASES]
)
def test_json_object_shell_path(
    python_only_path: str, case_id: str, args: list[str], expected: dict[str, object] | None, exit_class: str
) -> None:
    _assert_object_case(_run_object(args, python_only_path), expected, exit_class)


@pytest.mark.skipif(not HAS_JQ, reason="compares against jq itself; the shell half above runs everywhere")
@pytest.mark.parametrize(
    ("case_id", "args", "expected", "exit_class"), _OBJECT_CASES, ids=[c[0] for c in _OBJECT_CASES]
)
def test_json_object_jq_path(
    case_id: str, args: list[str], expected: dict[str, object] | None, exit_class: str
) -> None:
    _assert_object_case(_run_object(args, os.environ["PATH"]), expected, exit_class)


def test_json_object_shell_path_drops_c0_control_characters(python_only_path: str) -> None:
    """Documented parity gap (NFR01): _json_escape strips C0 controls other than tab/newline."""
    result = _run_object(["--str", "a", "x\x01y"], python_only_path)
    assert result.returncode == 0
    assert json.loads(result.stdout.decode("utf-8")) == {"a": "xy"}


def test_json_object_jq_and_shell_paths_parse_to_equal_objects(tmp_path: Path) -> None:
    """NFR01 parity: for every non-control-char case, jq and the shell builder agree."""
    if not shutil.which("jq"):
        pytest.skip("jq not installed")
    path_no_jq = path_without(tmp_path, {"jq"})
    for _case_id, args, expected, exit_class in _OBJECT_CASES:
        if exit_class != _OK:
            continue
        jq_result = _run_object(args, os.environ["PATH"])
        shell_result = _run_object(args, path_no_jq)
        assert json.loads(jq_result.stdout) == json.loads(shell_result.stdout) == expected
