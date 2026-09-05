"""Sanitized subprocess environment for dispatched coding-agent CLIs.

Belongs to the ``trw_mcp.dispatch`` package. ``build_subprocess_env`` returns a
*minimal allowlisted* environment for the child agent — it NEVER passes the host
``os.environ`` wholesale.

Why an allowlist (security, not convenience): a dispatched child agent is an
arbitrary LLM that can read its own environment and emit it in stdout. Inheriting
the full host environment would let a prompt-injected child exfiltrate every host
secret (AWS keys, DB URLs, unrelated provider tokens) through its answer. We pass
only the variables the child legitimately needs: a base set required to run any
CLI, plus the per-client provider API keys for the model it will call — read from
that client's registry entry rather than from a second table keyed on client id.

This mirrors the trw-loop ``worker_subprocess_env`` allowlist discipline and the
``.claude/rules/trw-mcp-python.md`` Subprocess Env Hygiene rule (full-env
inheritance enabled secret exfiltration via probe stdout, 2026-06-11).
"""

from __future__ import annotations

import os

from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for
from trw_mcp.dispatch._types import DispatchClient

# Always-safe base variables every CLI needs to locate binaries, resolve $HOME,
# and render output correctly. Deliberately excludes anything secret-bearing.
_BASE_ALLOWLIST: tuple[str, ...] = ("PATH", "HOME", "LANG", "TERM", "USER")

# Locale variables are passed by prefix match (LC_ALL, LC_CTYPE, ...).
_LOCALE_PREFIX = "LC_"

# Variables the INTERMEDIATE ``_run_job`` child needs to ``python -m trw_mcp...``
# successfully: PYTHONPATH (so an editable/source checkout resolves trw_mcp) and
# VIRTUAL_ENV (so the active venv is honored). These are NOT secret-bearing; they
# are forwarded ON TOP of the per-client allowlist by ``build_runner_env`` only.
_RUNNER_PASSTHROUGH: tuple[str, ...] = ("PYTHONPATH", "VIRTUAL_ENV")

# Per-client provider credentials are NOT listed here. They live on the client's
# registry entry (``ClientSpec.credential_env``) alongside the flags and the
# verification record, so one entry states everything TRW believes about a client
# and this module carries no client-id literal (PRD-CORE-266-NFR03/NFR04). A
# client with no recorded credential variable — because none appears in its cited
# source — therefore receives the base allowlist ALONE by construction, rather
# than by someone remembering to omit it from a second table.


def _allowed_names(client: DispatchClient) -> set[str]:
    """Return the full set of env-var names allowed for *client*.

    An unregistered client id yields the base allowlist alone. That is the
    fail-closed direction: an id TRW does not know gets FEWER variables, never a
    wider set and never the host environment.
    """
    try:
        credentials = client_spec_for(client).credential_env
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        credentials = ()
    return {*_BASE_ALLOWLIST, *credentials}


def build_subprocess_env(
    client: DispatchClient,
    source_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a sanitized env for launching *client*.

    Args:
        client: Which CLI is being launched (selects the API-key allowlist).
        source_env: Environment to filter (defaults to ``os.environ``). Injected
            for testability — tests plant a fake secret and assert it is absent.

    Returns:
        A new dict containing only allowlisted variables that are actually set
        in *source_env*. No secret outside the allowlist can reach the child.
    """
    src = dict(os.environ) if source_env is None else source_env
    allowed = _allowed_names(client)
    return {name: value for name, value in src.items() if name in allowed or name.startswith(_LOCALE_PREFIX)}


def build_probe_env(source_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build the env for a read-only CAPABILITY PROBE (e.g. ``<binary> --version``).

    The base allowlist and locale variables only — deliberately NOT the client's
    credential set. A probe exists to make the binary print a banner; forwarding
    an API key to it would widen the blast radius of a diagnostic for no benefit,
    and the doctor payload that quotes the probe's output must be safe to read.

    Uses the empty-credential path of :func:`_allowed_names` by construction
    rather than by re-listing the base set, so a variable added to the base
    allowlist reaches probes too.
    """
    src = dict(os.environ) if source_env is None else source_env
    allowed = set(_BASE_ALLOWLIST)
    return {name: value for name, value in src.items() if name in allowed or name.startswith(_LOCALE_PREFIX)}


def build_runner_env(
    client: DispatchClient,
    source_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the env for the INTERMEDIATE ``_run_job`` child of a background job.

    The background path launches ``python -m trw_mcp.dispatch._run_job`` as a
    detached intermediate, which then runs :func:`dispatch` (spawning the foreign
    agent CLI). That intermediate must NOT inherit the full host ``os.environ``
    (every secret) — but it needs slightly more than the bare client env:

    - the per-client allowlist from :func:`build_subprocess_env` (so the foreign
      agent it spawns still gets its provider key), PLUS
    - ``PYTHONPATH`` / ``VIRTUAL_ENV`` when set, so the ``python -m trw_mcp...``
      import resolves in an editable / source / venv checkout.

    No host secret outside the client allowlist can reach the runner (and thus
    the foreign agent) through this env.
    """
    src = dict(os.environ) if source_env is None else source_env
    env = build_subprocess_env(client, source_env=src)
    for name in _RUNNER_PASSTHROUGH:
        value = src.get(name)
        if value is not None:
            env[name] = value
    return env
