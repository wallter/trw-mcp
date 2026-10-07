"""``trw-mcp config dispatch``: the installer's dispatch setup step (PRD-INFRA-210 FR07, FR09, FR10).

Decides whether ``trw_dispatch`` is on (flag, prior answer, one prompt, or default off), applies per-client
model and effort pins, and returns one structured result that both the human line and the headless JSON
render. Every write goes through :func:`trw_mcp.tools._config_writer.set_config_value` at machine scope.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from trw_mcp.dispatch._targets import resolved_dispatch_defaults
from trw_mcp.tools._config_writer import ConfigSetRefusedError, set_config_value

__all__ = ["run_dispatch_step", "run_dispatch_verb"]


def _dispatch_cfg(target: Path) -> Any:
    from trw_mcp.models.config._loader import _build_config_unguarded

    return _build_config_unguarded(target / ".trw" / "config.yaml").dispatch


def _effort_problem(client: str, level: str) -> str:
    from trw_mcp.dispatch._client_specs import client_spec_for

    spec = client_spec_for(client)
    if not (spec.effort_flag or spec.effort_config_key):
        return f"{client} has no effort carrier"
    if level not in spec.effort_levels:
        return f"effort {level!r} is not one of {', '.join(spec.effort_levels)} for {client}"
    return ""


def _model_problem(model: str) -> str:
    return "whitespace" if any(ch.isspace() for ch in model) else ""


def _parse_pins(pairs: list[str], source: str, kind: str, warnings: list[str]) -> dict[str, str]:
    """``CLIENT=VALUE`` pairs to ``{client: value}``; a bad pair warns once and is skipped."""
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    pins: dict[str, str] = {}
    for pair in pairs:
        client, eq, value = (part.strip() for part in pair.partition("="))
        problem = ""
        if not eq or not client or not value:
            problem = f"malformed {kind} pin {pair!r} (expected CLIENT=VALUE)"
        elif client not in CLIENT_SPECS:
            problem = f"unknown client {client!r} in {kind} pin"
        elif kind == "model" and _model_problem(value):
            problem = f"model for {client} has whitespace"
        elif kind == "effort":
            problem = _effort_problem(client, value)
        if problem:
            warnings.append(f"{source}: {problem}; skipped")
        else:
            pins[client] = value
    return pins


def _env_pairs(name: str) -> list[str]:
    return [item for item in os.environ.get(name, "").split(",") if item.strip()]


def _ask_line(question: str, preface: str = "") -> str | None:
    """One typed line from the controlling terminal; ``None`` when there is no terminal or the run is headless."""
    from trw_mcp.tools._assess_cli import _non_interactive

    if _non_interactive():
        return None
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            if preface:
                tty.write(preface)
            tty.write(question)
            tty.flush()
            return tty.readline().strip()
    except OSError:  # trw-fail-silent-allow: no controlling terminal means no pin prompt; every value is kept
        return None


def _ask_valid(question: str, preface: str, problem: Callable[[str], str]) -> str:
    """The answer to *question* when it passes *problem* (``""`` = fine); Enter, none, or a second bad answer keeps ``""``."""
    for attempt in range(2):
        answer = _ask_line(question, preface if attempt == 0 else "")
        if not answer:
            return ""
        if not problem(answer):
            return answer
    return ""


def _prompt_pins(
    rows: list[dict[str, object]], skip_models: set[str], skip_efforts: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Per installed client ask for a model and (effort carriers only) an effort; Enter keeps the shown value."""
    from trw_mcp.dispatch._client_specs import client_spec_for

    models: dict[str, str] = {}
    efforts: dict[str, str] = {}
    for row in rows:
        client = str(row["client"])
        if not row["installed"]:
            continue
        shown = (
            f"{client}: model {row['model'] or '(its own)'} [{row['model_source']}], "
            f"effort {row['effort'] or 'none'} [{row['effort_source']}]\n"
        )
        if client not in skip_models:
            answer = _ask_valid(f"{client} model (Enter keeps): ", shown, _model_problem)
            if answer:
                models[client] = answer
        spec = client_spec_for(client)
        if client in skip_efforts or not (spec.effort_flag or spec.effort_config_key):
            continue
        answer = _ask_valid(
            f"{client} effort [{'|'.join(spec.effort_levels)}] (Enter keeps): ",
            "" if client not in skip_models else shown,
            partial(_effort_problem, client),
        )
        if answer:
            efforts[client] = answer
    return models, efforts


def run_dispatch_step(
    target: Path,
    *,
    offer: bool = False,
    enable: bool = False,
    disable: bool = False,
    model_pins: list[str] | None = None,
    effort_pins: list[str] | None = None,
) -> dict[str, object]:
    """Decide and apply the dispatch setup for *target*; returns the one structured result.

    Decision order: ``enable``/``disable`` (flag); else a prior operator answer in any layer or the
    environment (reused, never asked again); else, with ``offer`` on a terminal and at least two dispatch
    clients on PATH, one ``[y/N]`` question (default No); else off with nothing written. Every write is
    machine scope through :func:`set_config_value`. Pins from ``--dispatch-model`` / ``--dispatch-effort``
    and ``TRW_DISPATCH_MODELS`` / ``TRW_DISPATCH_EFFORTS`` apply whenever dispatch is on (a flag beats the
    environment, and either beats a typed answer); the per-client questions run only on a terminal when
    dispatch was just enabled by a flag or the prompt.
    """
    from trw_mcp.dispatch._setup import describe_dispatch
    from trw_mcp.tools._assess_cli import _ask_on_tty, _non_interactive

    target = target.resolve()
    writes: list[dict[str, object]] = []
    warnings: list[str] = []

    def write(key: str, value: object, raw: str) -> None:
        try:
            changed = set_config_value(key, raw, scope="machine", target_dir=target).changed
            outcome = "written" if changed else "unchanged"
        except (ConfigSetRefusedError, OSError) as exc:
            warnings.append(f"{key} not written: {exc}")
            outcome = "refused"
        writes.append({"key": key, "value": value, "outcome": outcome})

    cfg = _dispatch_cfg(target)
    installed = [str(r["client"]) for r in resolved_dispatch_defaults(cfg) if r["installed"]]
    decided = "default"
    wanted: bool | None = None
    if enable or disable:
        decided, wanted = "flag", enable
    elif "dispatch_tools_exposed" in cfg.operator_set:
        decided = "prior"
    elif offer and not _non_interactive() and len(installed) >= 2:
        names = ", ".join(installed[:3]) + (", ..." if len(installed) > 3 else "")
        if _ask_on_tty(f"Enable trw_dispatch (lets an agent launch {names})?"):
            decided, wanted = "prompt", True
    if wanted is not None:
        write("dispatch_tools_exposed", wanted, "true" if wanted else "false")
    cfg = _dispatch_cfg(target)
    if wanted is not None and cfg.dispatch_tools_exposed != wanted:
        who = (
            "TRW_DISPATCH_TOOLS_EXPOSED in the environment"
            if "TRW_DISPATCH_TOOLS_EXPOSED" in os.environ
            else "a project layer"
        )
        warnings.append(
            f"dispatch_tools_exposed is {str(cfg.dispatch_tools_exposed).lower()} after the write: {who} wins"
        )
    if cfg.dispatch_tools_exposed:
        models = _parse_pins(_env_pairs("TRW_DISPATCH_MODELS"), "TRW_DISPATCH_MODELS", "model", warnings)
        models |= _parse_pins(model_pins or [], "--dispatch-model", "model", warnings)
        efforts = _parse_pins(_env_pairs("TRW_DISPATCH_EFFORTS"), "TRW_DISPATCH_EFFORTS", "effort", warnings)
        efforts |= _parse_pins(effort_pins or [], "--dispatch-effort", "effort", warnings)
        if offer and decided in {"flag", "prompt"} and not _non_interactive():
            asked_models, asked_efforts = _prompt_pins(list(resolved_dispatch_defaults(cfg)), set(models), set(efforts))
            models, efforts = asked_models | models, asked_efforts | efforts
        for client, model in models.items():
            write(f"dispatch_default_models.{client}", model, json.dumps(model))
        for client, level in efforts.items():
            write(f"dispatch_default_efforts.{client}", level, level)
        cfg = _dispatch_cfg(target)
    return describe_dispatch(cfg, decided, writes, warnings)


def run_dispatch_verb(args: argparse.Namespace) -> int:
    from trw_mcp.bootstrap._headless_report import redact_dispatch

    try:
        result = run_dispatch_step(
            args.target,
            offer=args.offer and not args.json,  # --json is a machine mode: it never asks
            enable=args.enable,
            disable=args.disable,
            model_pins=args.dispatch_model,
            effort_pins=args.dispatch_effort,
        )
    except OSError as exc:
        print(f"config dispatch: failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    result = redact_dispatch(result)
    for warning in result["warnings"]:
        print(f"config dispatch: warning: {warning}", file=sys.stderr)
    print(json.dumps(result) if args.json else result["line"])
    return 0
