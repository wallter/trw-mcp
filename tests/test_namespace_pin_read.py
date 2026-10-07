"""PRD-CORE-333 S3c: the light ``project_namespace`` read agrees with ``TRWConfig``, or refuses.

``selected_store`` reads the pin through ``state._namespace_pin_read.pinned_namespace``
instead of constructing ``TRWConfig``. The matrix below covers every combination of the
pin's sources -- the environment variable, the project layer and the machine layer, each
absent, set, null, of the wrong type or unreadable -- and holds the two to one rule:
the light read returns exactly what ``TRWConfig`` resolves, or it refuses. It refuses
whenever ``TRWConfig`` fails, and otherwise only where the answer is ambiguous.

A second matrix covers the keys that change how ``TRWConfig`` resolves its sources
(``_env_prefix`` and the other ``_``-prefixed settings arguments, the pin's key in another
case): there the read hands over to the full ``TRWConfig`` and must equal it exactly.
The last test pins what is NOT claimed: an unrelated invalid key does not block the pin.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._loader import resolve_config_overrides
from trw_mcp.state import _namespace_pin_read
from trw_mcp.state._namespace_pin_read import PinUnreadableError, pinned_namespace
from trw_mcp.state._store_selection import StoreUnavailableError, selected_store

REFUSED = "<refused>"

#: What the environment holds: name -> value, or nothing.
_ENVIRONMENTS: dict[str, dict[str, str]] = {
    "unset": {},
    "set": {"TRW_PROJECT_NAMESPACE": "project:from-env"},
    "set-empty": {"TRW_PROJECT_NAMESPACE": ""},
    "lower-case": {"trw_project_namespace": "project:lower"},
}
#: One config.yaml layer: None = no file (or no .trw at all, for the project layer).
_LAYERS: dict[str, str | None] = {
    "absent": None,
    "empty-file": "",
    "no-key": "hooks_enabled: true\n",
    "string": "project_namespace: project:LAYER\n",
    "null": "project_namespace: null\n",
    "integer": "project_namespace: 42\n",
    "list": "project_namespace: [a, b]\n",
    "mapping": "project_namespace: {nested: x}\n",
    "not-yaml": "project_namespace: [unclosed\n",
    "not-a-mapping": "- project_namespace\n",
    "unreadable": "project_namespace: project:LAYER\n",
}


def _write(path: Path, layer: str, label: str) -> None:
    text = _LAYERS[layer]
    if text is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("LAYER", label), encoding="utf-8")
    if layer == "unreadable":
        path.chmod(0)


def _outcome(read: object) -> str:
    try:
        return str(read())  # type: ignore[operator]
    except KeyboardInterrupt:
        raise
    except BaseException:  # justified: test oracle, any refusal (SystemExit from a CLI source too) is one outcome
        return REFUSED


def test_the_light_read_equals_trwconfig_or_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.upper() == "TRW_PROJECT_NAMESPACE"]:
        monkeypatch.delenv(name)
    cases = itertools.product(_ENVIRONMENTS, _LAYERS, _LAYERS)
    agreed = refused_alone = 0
    for index, (environment, project, machine) in enumerate(cases):
        home, root = tmp_path / f"{index}-home", tmp_path / f"{index}-repo"
        root.mkdir()
        _write(home / ".trw" / "config.yaml", machine, "machine")
        _write(root / ".trw" / "config.yaml", project, "project")
        trw_dir = root / ".trw"
        with monkeypatch.context() as patch:
            patch.setenv("HOME", str(home))
            for name, value in _ENVIRONMENTS[environment].items():
                patch.setenv(name, value)
            light = _outcome(lambda: pinned_namespace(trw_dir))
            full = _outcome(
                lambda: TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml")).project_namespace  # type: ignore[arg-type]
            )
        case = (environment, project, machine)
        if full == REFUSED:
            assert light == REFUSED, f"{case}: TRWConfig refuses but the light read returned {light!r}"
        elif light == REFUSED:
            # The only refusal TRWConfig does not share: the variable in another case, which it
            # reads while its cascade keeps the file key -- an answer that depends on spelling.
            assert environment == "lower-case", f"{case}: the light read refused where TRWConfig resolved {full!r}"
            refused_alone += 1
        else:
            assert light == full, case
            agreed += 1
    assert agreed >= 100 and refused_alone > 0


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.parametrize(
    "layer",
    [
        "not-yaml",
        "integer",
        pytest.param("unreadable", marks=pytest.mark.skipif(_ROOT, reason="root ignores mode bits")),
    ],
)
def test_an_unreadable_pin_fails_closed_in_store_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layer: str
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    _write(tmp_path / "repo" / ".trw" / "config.yaml", layer, "project")

    with pytest.raises(PinUnreadableError if layer == "integer" else StateError):
        pinned_namespace(tmp_path / "repo" / ".trw")
    with pytest.raises(StoreUnavailableError, match="project_namespace is unreadable"):
        selected_store(tmp_path / "repo" / ".trw")


def test_an_unreadable_pin_is_store_unavailable_in_the_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.state import _auto_recall_hook as hook

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    _write(tmp_path / "repo" / ".trw" / "config.yaml", "not-yaml", "project")

    assert (
        hook.main([str(tmp_path / "repo"), "wal reset recovery", str(tmp_path / "injected"), "3", "100", "0.35", "10"])
        == 0
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "decision=store_unavailable" in captured.err


def _full(trw_dir: Path) -> str:
    return TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml")).project_namespace  # type: ignore[arg-type]


#: Lines that change how TRWConfig resolves its sources, and the layer they sit in.
_CONTROLS: dict[str, tuple[str, str]] = {
    "none": ("project", ""),
    "project _env_prefix": ("project", "_env_prefix: ALT_\n"),
    "machine _env_prefix": ("machine", "_env_prefix: ALT_\n"),
    "project _case_sensitive": ("project", "_case_sensitive: true\n"),
    "project _env_ignore_empty": ("project", "_env_ignore_empty: true\n"),
    "upper-case key": ("project", "PROJECT_NAMESPACE: project:upper\n"),
    "mixed-case key": ("machine", "Project_Namespace: project:mixed\n"),
    "project _cli_parse_args": ("project", '_cli_parse_args: ["--unknown-review-option"]\n'),
    "machine _cli_parse_args": ("machine", "_cli_parse_args: true\n"),
}
_CONTROL_ENVIRONMENTS: dict[str, dict[str, str]] = {
    "unset": {},
    "trw": {"TRW_PROJECT_NAMESPACE": "project:A"},
    "trw-empty": {"TRW_PROJECT_NAMESPACE": ""},
    "alt": {"ALT_PROJECT_NAMESPACE": "project:B"},
    "both": {"TRW_PROJECT_NAMESPACE": "project:A", "ALT_PROJECT_NAMESPACE": "project:B"},
}


def test_a_source_control_key_hands_the_pin_to_trwconfig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 P1: wherever a layer changes how sources resolve, the read IS TRWConfig's; elsewhere it is not."""
    for name in [name for name in os.environ if name.upper().endswith("_PROJECT_NAMESPACE")]:
        monkeypatch.delenv(name)
    handed_over: list[Path] = []
    real = _namespace_pin_read._full_resolution
    monkeypatch.setattr(_namespace_pin_read, "_full_resolution", lambda d: handed_over.append(d) or real(d))
    pins = ("", "project_namespace: project:LAYER\n")
    cases = itertools.product(_CONTROLS, _CONTROL_ENVIRONMENTS, pins, pins)
    for index, (control, environment, project_pin, machine_pin) in enumerate(cases):
        where, line = _CONTROLS[control]
        texts = {"project": project_pin.replace("LAYER", "project"), "machine": machine_pin.replace("LAYER", "machine")}
        texts[where] += line
        home, root = tmp_path / f"{index}-home", tmp_path / f"{index}-repo"
        for path, text in (
            (home / ".trw" / "config.yaml", texts["machine"]),
            (root / ".trw" / "config.yaml", texts["project"]),
        ):
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
        handed_over.clear()
        with monkeypatch.context() as patch:
            patch.setenv("HOME", str(home))
            for name, value in _CONTROL_ENVIRONMENTS[environment].items():
                patch.setenv(name, value)
            light = _outcome(lambda: pinned_namespace(root / ".trw"))
            full = _outcome(lambda: _full(root / ".trw"))
        case = (control, environment, project_pin, machine_pin)
        if "_cli_" in control:
            # TRWConfig's answer here depends on the process's own argv (pytest's, or an xdist
            # worker's); a store read never parses argv, so it refuses whatever argv holds.
            assert light == REFUSED, f"{case}: a _cli_* control resolved to {light!r}"
        else:
            assert light == full, f"{case}: light {light!r} != TRWConfig {full!r}"
        assert bool(handed_over) is (control != "none"), case


def test_the_reviewers_env_prefix_repro_opens_trwconfigs_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_env_prefix: ALT_`` makes TRWConfig read ALT_PROJECT_NAMESPACE (B); the read must not answer TRW_... (A)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRW_PROJECT_NAMESPACE", "project:A")
    monkeypatch.setenv("ALT_PROJECT_NAMESPACE", "project:B")
    config = tmp_path / "repo" / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("_env_prefix: ALT_\n", encoding="utf-8")

    assert _full(config.parent) == "project:B"
    assert pinned_namespace(config.parent) == "project:B"


def test_an_unrelated_invalid_key_does_not_block_the_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r2 P2, option (b): the read validates the pin only; TRWConfig's refusal of another key is not shared."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    config = tmp_path / "repo" / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("project_namespace: project:pinned\nhooks_enabled: not-a-bool\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hooks_enabled"):
        _full(config.parent)
    assert pinned_namespace(config.parent) == "project:pinned"


def test_a_cli_control_key_is_a_refusal_not_a_process_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review r3: ``_cli_parse_args`` made the full TRWConfig parse argv and raise SystemExit(2) through selected_store."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    config = tmp_path / "repo" / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text('project_namespace: project:x\n_cli_parse_args: ["--unknown-review-option"]\n', encoding="utf-8")

    with pytest.raises(PinUnreadableError, match="command line"):
        pinned_namespace(config.parent)
    with pytest.raises(StoreUnavailableError, match="project_namespace is unreadable"):
        selected_store(config.parent)
