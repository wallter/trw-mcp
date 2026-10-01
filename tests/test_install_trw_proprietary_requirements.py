"""The installer judges a private requirement's version and marker, not just its name.

PROPRIETARY-PRIVATE-VERSION-CHECK (codex DISTILL-0.10 r1 KI2): a wheel requiring ``trw-llm>=99`` passed with any
``trw-llm`` present, because pip no longer sees the private constraint (the wheel installs ``--no-index --no-deps``).
PROPRIETARY-MARKER-EVAL (KI4): ``_wheel_requirements`` dropped every requirement whose marker merely mentioned
``extra``, including compound markers that are true with no extras, and counted nothing else.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module

_PIP = "/opt/py/bin/python3"


def _wheel(directory: Path, name: str, version: str, requires: list[str]) -> Path:
    dist = name.replace("-", "_")
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    meta = "".join(f"Requires-Dist: {r}\n" for r in requires)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"{dist}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{meta}"
        )
    return path


@pytest.fixture(params=_INSTALLER_PATHS, ids=["template", "artifact"])
def module(request: pytest.FixtureRequest):
    return _load_installer_module(request.param)


def test_a_version_outside_the_specifier_is_reported_with_what_is_installed(module, tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm>=99"])

    assert module._missing_proprietary_requirements(wheel, {"trw-llm": "0.1.0"}) == ["trw-llm>=99 (have 0.1.0)"]
    assert module._missing_proprietary_requirements(wheel, {"trw-llm": "99.1"}) == []


@pytest.mark.parametrize(
    ("requirement", "version", "expected"),
    [
        ("trw-llm>=0.1.0,<1", "0.5.2", True),
        ("trw-llm>=0.1.0,<1", "1.0", False),
        ("trw-llm (>=0.1.0)", "0.1.0", True),
        ("trw-llm==0.1.*", "0.1.9", True),
        ("trw-llm==0.1.*", "0.2.0", False),
        ("trw-llm~=0.1.2", "0.1.9", True),
        ("trw-llm~=0.1.2", "0.2.0", False),
        ("trw-llm!=0.1.0", "0.1.0", False),
        ("trw-llm", "0.0.1", True),
        ("trw-llm>=0.1.0 ; python_version >= '3.11'", "0.0.1", False),
    ],
)
def test_specifier_clauses(module, requirement: str, version: str, expected: bool) -> None:
    assert module._requirement_satisfied_by(requirement, version) is expected


def test_a_direct_reference_cannot_be_verified_so_it_is_not_satisfied(module, tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm @ https://example.invalid/trw_llm-0.1.0.whl"])

    assert module._missing_proprietary_requirements(wheel, {"trw-llm": "0.1.0"}) == [
        "trw-llm@ https://example.invalid/trw_llm-0.1.0.whl (have 0.1.0)"
    ]


def test_a_set_of_names_without_versions_is_still_checked_by_name(module, tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm>=99"])

    assert module._missing_proprietary_requirements(wheel, set()) == ["trw-llm"]
    assert module._missing_proprietary_requirements(wheel, {"trw-llm"}) == []


def test_only_a_requirement_that_needs_an_extra_is_dropped(module, tmp_path: Path) -> None:
    wheel = _wheel(
        tmp_path,
        "trw-distill",
        "0.10.0",
        [
            'trw-llm>=0.1.0 ; python_version >= "3.0" or extra == "dev"',  # an alternative needs no extra: stays
            'trw-metaharness>=0.1.0 ; extra == "dev"',  # every alternative needs the extra: dropped
            'trw-loop>=0.1.0 ; python_version < "3.0" and extra == "dev"',  # the only alternative needs it: dropped
            'trw-swarm>=0.1.0 ; python_version < "3.0"',  # an OS or Python-version condition never drops (pip re-evaluates)
            "requests>=2",
        ],
    )

    names = [name for name, _ in module._wheel_requirements(wheel)]

    assert names == ["trw-llm", "trw-swarm", "requests"]
    # An extra-only private requirement does not count toward the missing check.
    assert module._missing_proprietary_requirements(wheel, set()) == ["trw-llm", "trw-swarm"]


@pytest.mark.parametrize(
    "marker",
    [
        '(python_version >= "3.0" and extra == "dev")',  # parenthesised: never judged
        "platform_version == \"a or extra == 'x'\"",  # a quoted string holding the keywords is not an atom
        "extra == dev",  # unquoted: not a plain extra test
        '"dev" == extra',  # reversed operands: not judged
    ],
)
def test_a_marker_the_evaluator_cannot_read_keeps_the_requirement(module, tmp_path: Path, marker: str) -> None:
    wheel = _wheel(tmp_path, "trw-distill", "0.10.0", [f"trw-llm>=0.1.0 ; {marker}"])

    assert [name for name, _ in module._wheel_requirements(wheel)] == ["trw-llm"]


def test_deeply_nested_markers_do_not_crash_the_installer(module, tmp_path: Path) -> None:
    """codex r1 KI: attacker-controlled METADATA aborted the private-install phase with a RecursionError."""
    wheel = _wheel(
        tmp_path, "trw-distill", "0.10.0", ["trw-llm>=0.1.0 ; " + "(" * 20000 + 'extra == "x"' + ")" * 20000]
    )

    assert [name for name, _ in module._wheel_requirements(wheel)] == ["trw-llm"]


def test_a_semicolon_inside_a_direct_reference_url_does_not_hide_it(module, tmp_path: Path) -> None:
    """codex r1 KI: ``;`` in the URL was read as a marker separator, which dropped the reference unjudged."""
    wheel = _wheel(
        tmp_path,
        "trw-distill",
        "0.10.0",
        [
            'trw-llm @ https://example.invalid/a;extra=="x".whl',
            'trw-metaharness @ https://example.invalid/b ; extra == "dev"',
        ],
    )

    assert [name for name, _ in module._wheel_requirements(wheel)] == ["trw-llm"]  # the second one needs an extra
    assert module._missing_proprietary_requirements(wheel, {"trw-llm": "0.1.0"}) != []  # and the first is unverifiable


@pytest.mark.parametrize(
    ("requirement", "version", "expected"),
    [
        ("trw-llm<1", "1.0rc1", False),  # PEP 440: <V does not admit V's own pre-releases
        ("trw-llm<1", "0.9", True),
        ("trw-llm>1", "1.0.post1", False),  # >V does not admit V's own post-releases
        ("trw-llm>1", "1.1", True),
        ("trw-llm>=0.1.0", "0.1.0+g123", True),  # a local version label is ignored
        ("trw-llm==0.1.*", "0.1.0+g123", True),
        ("trw-llm==abc.*", "abc.1", False),  # the wildcard prefix and the version are validated
        ("trw-llm==0.1.*", "weird", False),
        ("trw-llm==0.1rc1.*", "0.1rc1.2", False),  # a wildcard prefix must be a release-only version
        ("trw-llm>=1!2", "3", False),  # an epoch is not judged, so it is refused
        ("trw-llm<1.0rc2", "1.0rc1", True),  # the bound is itself a pre-release, so its earlier pre-releases pass
        ("trw-llm>1.0rc1", "1.0", True),  # a final release of the same version is not a post-release
        ("trw-llm>1.0.post1", "1.0.post2", True),
        ("trw-llm>=0.1.0", "0.1.0+", False),  # a malformed local label is refused, not stripped
        ("trw-llm>=0.1.0", "0.1.0+!!", False),
        ("trw-llm==0.1.0+g1", "0.1.0", False),  # a local label in the bound is not judged
        ("trw-llm==0.1rc1.*", "0.1rc1.2", False),  # the wildcard prefix must be release-only
        ("trw-llm>=1" + "0" * 5000, "1", False),  # an oversized component is refused, not an uncaught error
        ("trw-llm>=1", "1" + "0" * 5000, False),
    ],
)
def test_clause_edges_follow_pep_440_or_fail_closed(module, requirement: str, version: str, expected: bool) -> None:
    assert module._requirement_satisfied_by(requirement, version) is expected


def test_a_prerequisite_below_the_required_version_refuses_its_dependent(
    module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: trw-distill needs trw-llm>=99, only 0.1.0 installs, so trw-distill is refused."""
    wheels = {
        "trw-llm": _wheel(tmp_path, "trw-llm", "0.1.0", []),
        "trw-metaharness": _wheel(tmp_path, "trw-metaharness", "0.1.5", []),
        "trw-distill": _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm>=99", "trw-metaharness>=0.1.5"]),
        "trw-loop": _wheel(tmp_path, "trw-loop", "0.3.7", []),
        "trw-swarm": _wheel(tmp_path, "trw-swarm", "0.5.0", []),
    }
    versions = {name: path.name.split("-")[1] for name, path in wheels.items()}
    monkeypatch.setattr(
        module,
        "_post_proprietary_entitlement",
        lambda _url, _key, pkg, _ver: {"version": versions[pkg], "sha256": "x", "url": f"https://h/{pkg}"},
    )
    monkeypatch.setattr(module, "_download_proprietary_wheel", lambda url, *_a: wheels[url.rsplit("/", 1)[1]])
    attempted: list[str] = []

    def fake_install(_python, _wheel_path, package, *_a, **_k):
        attempted.append(package)
        return True

    monkeypatch.setattr(module, "_install_proprietary_wheel", fake_install)
    monkeypatch.setattr(module, "_emit_install_completed_event", lambda *_a: None)
    monkeypatch.setattr(module, "_write_proprietary_console_wrappers", lambda *_a: [])
    ui = MagicMock()

    installed = module.phase_install_proprietary(
        ui, 1, 1, _PIP, "key", {}, "https://b", auto_confirm=True, project_dir=tmp_path
    )

    assert "trw-distill" not in attempted
    assert all(not entry.startswith("trw-distill") for entry in installed)
    warned = " ".join(str(call.args[0]) for call in ui.step_warn.call_args_list)
    assert "trw-llm>=99 (have 0.1.0)" in warned


@pytest.mark.parametrize(
    ("marker", "active"),
    [
        ('extra == ""', True),  # extra is "" with no extras selected, so this one is TRUE: the requirement stays
        ('extra == "x"', False),
        ('extra == "a" or extra == "b"', False),
        ('python_version > "3" and extra == "x"', False),
        ('python_version > "3" or extra == "x"', True),
        ("platform_version == \"a and extra == 'y' or\"", True),  # quoted text holding keywords is not an operator
        ("platform_version == 'x y' and extra == 'z'", True),  # a quoted string with spaces is not judged
        ('extra == "x" and', True),  # a dangling operator is a malformed marker: not judged
        ('and extra == "x"', True),
        ('extra == "x" or', True),
        ('extra == "x" or or extra == "y"', True),
        ("", True),
    ],
)
def test_marker_activity_with_no_extras(module, marker: str, active: bool) -> None:
    assert module._marker_is_active(marker) is active


def test_long_runs_of_whitespace_do_not_make_the_checks_quadratic(module) -> None:
    import time

    spaces = " " * 300_000
    started = time.monotonic()
    module._marker_is_active(spaces + 'extra == "x"')
    module._split_requirement("trw-llm" + spaces)
    module._requirement_satisfied_by("trw-llm" + spaces + ">=1", "1")
    assert time.monotonic() - started < 2
