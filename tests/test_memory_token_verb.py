"""PRD-CORE-298 FR02 -- ``trw-mcp memory token`` mints this checkout's grant, and only it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("trw_memory.daemon")

from trw_memory.daemon import DaemonPaths
from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH, granted_namespaces, read_grant

from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

_FOREIGN = "project:beta-22222222"


def _pin(root: Path, namespace: str) -> None:
    (root / ".trw" / "config.yaml").write_text(f"project_namespace: {namespace}\n", encoding="utf-8")


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout pinned to the namespace derived from its own location, as ``memory migrate`` pins it."""
    from trw_memory.namespaces.identity import resolve_project_identity

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    root = tmp_path / "repo"
    (root / ".trw").mkdir(parents=True)
    _pin(root, resolve_project_identity(root).namespace)
    return root


def _derived(root: Path) -> str:
    from trw_memory.namespaces.identity import resolve_project_identity

    return resolve_project_identity(root).namespace


def _run(*argv: str) -> None:
    args = _build_arg_parser().parse_args(["memory", *argv])
    SUBCOMMAND_HANDLERS[args.command](args)


def test_the_grant_is_exactly_the_pinned_namespace_and_user_local(
    checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run("token", "--target-dir", str(checkout))

    token_path = checkout / CHECKOUT_TOKEN_RELPATH
    token = token_path.read_text(encoding="utf-8").strip()
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert granted_namespaces(DaemonPaths.resolve(), token) == frozenset({_derived(checkout), "user:local"})
    assert read_grant(DaemonPaths.resolve(), token).root == str(checkout.resolve()), "file tools reach only this tree"  # type: ignore[union-attr]
    assert token not in capsys.readouterr().out, "the raw token is never printed"


def test_a_foreign_grant_is_refused_and_the_grants_file_is_untouched(checkout: Path) -> None:
    _run("token", "--target-dir", str(checkout))
    grants = DaemonPaths.resolve().grants
    before = grants.read_bytes()

    with pytest.raises(SystemExit) as exit_info:
        _run("token", "--target-dir", str(checkout), "--grant", _FOREIGN)

    assert exit_info.value.code not in (0, None)
    assert grants.read_bytes() == before


def test_migrate_deletes_the_slice_a_token_and_mints_only_this_checkout(checkout: Path) -> None:
    paths = DaemonPaths.resolve()
    paths.token.write_text("an-all-namespace-bearer", encoding="utf-8")

    _run("token", "--target-dir", str(checkout), "--migrate")

    assert not paths.token.exists()
    assert list(json.loads(paths.grants.read_text(encoding="utf-8")).values()) == [
        {"namespaces": sorted([_derived(checkout), "user:local"]), "root": str(checkout.resolve())}
    ]


def test_an_unpinned_checkout_mints_nothing(checkout: Path) -> None:
    (checkout / ".trw" / "config.yaml").write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        _run("token", "--target-dir", str(checkout))

    assert exit_info.value.code not in (0, None)
    assert not DaemonPaths.resolve(create=False).grants.exists()
    assert not (checkout / CHECKOUT_TOKEN_RELPATH).exists()


def test_doctor_names_the_migration_while_a_slice_a_token_remains(checkout: Path) -> None:
    from trw_mcp.server._doctor_memory_daemon import memory_daemon_row

    paths = DaemonPaths.resolve()
    paths.token.write_text("an-all-namespace-bearer", encoding="utf-8")

    status, message = memory_daemon_row()

    assert status == "WARN"
    assert "trw-mcp memory token --migrate" in message


def test_a_pin_edited_to_another_project_mints_nothing(checkout: Path) -> None:
    """The pin is editable config: without an explicit --namespace it never mints another project's grant."""
    _pin(checkout, _FOREIGN)

    with pytest.raises(SystemExit) as exit_info:
        _run("token", "--target-dir", str(checkout))

    assert f"--namespace {_FOREIGN}" in str(exit_info.value.code)
    assert not DaemonPaths.resolve(create=False).grants.exists()
    assert not (checkout / CHECKOUT_TOKEN_RELPATH).exists()


def test_a_moved_checkout_mints_its_pin_when_the_operator_names_it(checkout: Path) -> None:
    _pin(checkout, _FOREIGN)

    _run("token", "--target-dir", str(checkout), "--namespace", _FOREIGN)

    token = (checkout / CHECKOUT_TOKEN_RELPATH).read_text(encoding="utf-8").strip()
    assert granted_namespaces(DaemonPaths.resolve(), token) == frozenset({_FOREIGN, "user:local"})


def test_naming_a_namespace_other_than_the_pin_mints_nothing(checkout: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _run("token", "--target-dir", str(checkout), "--namespace", _FOREIGN)

    assert _FOREIGN in str(exit_info.value.code)
    assert not DaemonPaths.resolve(create=False).grants.exists()


def test_a_trw_project_namespace_env_override_mints_nothing_for_the_env_value(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C12 finding 1: an env override must never mint a grant for a namespace this checkout's own pin does not name.

    ``resolve_config_overrides`` excludes any key shadowed by a ``TRW_*`` env
    var so ``TRWConfig`` falls through to the env value -- if minting read the
    namespace through that cascade instead of the written pin, an operator's
    ``TRW_PROJECT_NAMESPACE`` naming ANOTHER project's namespace would silently
    mint a grant for it. Minting reads the written pin directly and refuses
    outright when the env value disagrees with the pin.
    """
    monkeypatch.setenv("TRW_PROJECT_NAMESPACE", _FOREIGN)

    with pytest.raises(SystemExit) as exit_info:
        _run("token", "--target-dir", str(checkout))

    assert exit_info.value.code not in (0, None)
    assert _FOREIGN in str(exit_info.value.code)
    assert not DaemonPaths.resolve(create=False).grants.exists()
    assert not (checkout / CHECKOUT_TOKEN_RELPATH).exists()
