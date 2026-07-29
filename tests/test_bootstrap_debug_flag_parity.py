"""No client bootstrap profile may bake ``--debug`` into a generated MCP entry.

Until 2026-07-27 the codex, cursor, and opencode profiles hardcoded ``--debug``
while the generic ``.mcp.json`` entry (claude-code), copilot, and antigravity-cli
did not. The same install therefore logged different things depending on which
client spawned the server: on Claude Code every ``logger.debug`` call was dropped
by ``structlog.make_filtering_bound_logger`` before any processor ran, and no
file sink was opened at all.

That is a client profile changing *protocol*, not surface density — the exact
inversion ``docs/VISION.md`` Principle 9 ("Integrate Rather Than Captivate")
warns about. Log verbosity is now a single portable toggle:
``.trw/config.yaml`` ``debug: true`` (see ``_logging.configure_logging``).

These tests pin BOTH halves of that contract:
  * no profile emits ``--debug`` (the parity half), and
  * the profiles agree with each other (the sameness half) — a future profile
    that quietly adds a verbosity flag fails here even if it is not ``--debug``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Flags that change what the server logs. A client profile must never set one:
# they belong to the operator, via config or env, applied to every client alike.
_VERBOSITY_FLAGS = frozenset({"--debug", "-v", "-vv", "-vvv", "--verbose", "--quiet", "-q", "--log-level"})


def _entry_tokens(entry: Any) -> list[str]:
    """Flatten a generated server entry's command + args into a token list."""
    tokens: list[str] = []
    for key in ("command", "args"):
        value = entry.get(key) if isinstance(entry, dict) else None
        if isinstance(value, str):
            tokens.append(value)
        elif isinstance(value, list):
            tokens.extend(str(item) for item in value)
    return tokens


def _all_profile_entries(*, on_path: bool) -> dict[str, Any]:
    """Build every client profile's MCP server entry under one PATH condition.

    *on_path* selects the ``shutil.which("trw-mcp")`` branch each profile takes,
    because the fallback branches historically carried their own copy of the
    flag (``_opencode.py`` and ``_codex.py`` each hardcoded it twice).
    """
    from trw_mcp.bootstrap._codex import _trw_mcp_server_entry as codex_entry
    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor as cursor_entry
    from trw_mcp.bootstrap._opencode import _get_trw_mcp_entry as opencode_entry
    from trw_mcp.bootstrap._utils import _trw_mcp_server_entry as generic_entry
    from trw_mcp.channels.copilot._vscode_mcp import _TRW_MCP_SERVER_ENTRY as copilot_entry

    resolved = "/usr/local/bin/trw-mcp" if on_path else None
    with (
        patch("trw_mcp.bootstrap._utils.shutil.which", return_value=resolved),
        patch("trw_mcp.bootstrap._codex.shutil.which", return_value=resolved),
        patch("trw_mcp.bootstrap._cursor.shutil.which", return_value=resolved),
        patch("trw_mcp.bootstrap._opencode.shutil.which", return_value=resolved),
    ):
        entries: dict[str, Any] = {
            "claude-code (.mcp.json)": generic_entry(),
            "codex": codex_entry(),
            "cursor": cursor_entry(),
            "opencode": opencode_entry(),
        }
    # copilot's entry is a module constant, not PATH-dependent.
    entries["copilot (.vscode/mcp.json)"] = copilot_entry
    return entries


@pytest.mark.unit
@pytest.mark.parametrize("on_path", [True, False], ids=["binary-on-path", "python-module-fallback"])
def test_no_profile_bakes_a_verbosity_flag(on_path: bool) -> None:
    for name, entry in _all_profile_entries(on_path=on_path).items():
        tokens = set(_entry_tokens(entry))
        offenders = tokens & _VERBOSITY_FLAGS
        assert not offenders, (
            f"client profile {name!r} bakes {sorted(offenders)} into its generated MCP entry. "
            "Log verbosity is protocol, not per-client surface density — it belongs in "
            ".trw/config.yaml::debug, which applies to every client alike."
        )


@pytest.mark.unit
@pytest.mark.parametrize("on_path", [True, False], ids=["binary-on-path", "python-module-fallback"])
def test_profiles_agree_on_flags_beyond_the_launcher(on_path: bool) -> None:
    """Every profile passes the same (empty) flag set — only the launcher differs.

    Profiles legitimately differ in HOW they name the executable (console
    script vs ``python -m``, project venv vs PATH). They must not differ in the
    flags they hand it.
    """
    per_profile_flags = {
        name: sorted(tok for tok in _entry_tokens(entry) if tok.startswith("-") and tok != "-m")
        for name, entry in _all_profile_entries(on_path=on_path).items()
    }
    distinct = {tuple(flags) for flags in per_profile_flags.values()}
    assert distinct == {()}, f"client profiles disagree on server flags: {per_profile_flags}"


@pytest.mark.unit
def test_antigravity_profile_passes_no_verbosity_flag() -> None:
    """antigravity-cli builds its command through a separate resolver."""
    from trw_mcp.bootstrap._antigravity_cli import _resolve_trw_mcp_command

    command, args = _resolve_trw_mcp_command()
    assert not ({command, *args} & _VERBOSITY_FLAGS)


@pytest.mark.unit
def test_toml_value_renders_empty_list_as_valid_toml() -> None:
    """Regression: ``all()`` over [] is vacuously True.

    That routed an empty list into the inline-table branch and emitted
    ``[\\n  ,\\n]``, which is not valid TOML. Latent until the trw entry's
    ``args`` became empty.
    """
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib

    from trw_mcp.bootstrap._codex_toml import _toml_value

    rendered = _toml_value([])
    assert rendered == "[]"
    assert tomllib.loads(f"args = {rendered}") == {"args": []}


@pytest.mark.unit
def test_default_config_template_documents_the_toggle() -> None:
    """The generated ``.trw/config.yaml`` must tell the operator where the knob is.

    Removing ``--debug`` from every client config only helps if the replacement
    is discoverable, so the seeded config carries the instruction inline.
    """
    from trw_mcp.bootstrap import _default_config

    text = _default_config()
    assert "debug: false" in text
    comments = "\n".join(line for line in text.splitlines() if line.lstrip().startswith("#"))
    assert "--debug" in comments, "template must say no client config passes --debug"
    assert ".trw/logs" in comments, "template must name the file sink debug:true opens"
    assert "TRW_LOG_LEVEL" in comments, "template must name the env override"


class TestGeneratedConfigsOnDisk:
    """End-to-end: the files actually written to disk carry no --debug."""

    def test_codex_config_toml_has_no_debug(self, tmp_path: Path) -> None:
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover
            import tomli as tomllib

        from trw_mcp.bootstrap._codex import generate_codex_config

        generate_codex_config(tmp_path)
        raw = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
        assert "--debug" not in raw
        config = tomllib.loads(raw)
        assert config["mcp_servers"]["trw"]["args"] == []

    def test_cursor_mcp_json_has_no_debug(self, tmp_path: Path) -> None:
        import json

        from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

        generate_cursor_mcp_config(tmp_path)
        raw = (tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        assert "--debug" not in raw
        assert json.loads(raw)["mcpServers"]["trw"]["args"] == []

    def test_opencode_json_has_no_debug(self, tmp_path: Path) -> None:
        import json

        from trw_mcp.bootstrap._opencode import generate_opencode_config

        generate_opencode_config(tmp_path)
        raw = (tmp_path / "opencode.json").read_text(encoding="utf-8")
        assert "--debug" not in raw
        assert "--debug" not in json.loads(raw)["mcp"]["trw"]["command"]

    def test_refresh_migrates_a_legacy_debug_mcp_json_entry(self, tmp_path: Path) -> None:
        """A TRW-managed legacy ``args=["--debug"]`` entry is migrated on refresh.

        This is the intended migration for existing installs: the flag moves to
        ``.trw/config.yaml``. The next test pins that a user-CUSTOMIZED entry is
        NOT touched, so the migration cannot clobber a deliberate pin.
        """
        import json

        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(
            json.dumps({"mcpServers": {"trw": {"command": "trw-mcp", "args": ["--debug"]}}}),
            encoding="utf-8",
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        assert json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"]["trw"]["args"] == []

    def test_refresh_preserves_a_user_pinned_debug_entry(self, tmp_path: Path) -> None:
        """A pinned absolute-path entry (the dev-repo pattern) keeps its --debug.

        This repo's own .mcp.json is exactly this shape, and pre-release local
        development depends on it surviving ``update-project``.
        """
        import json

        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        binary = tmp_path / ".venv" / "bin" / "trw-mcp"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        original = {"mcpServers": {"trw": {"command": str(binary), "args": ["--debug"]}}}
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(original), encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        assert json.loads(mcp_path.read_text(encoding="utf-8")) == original
