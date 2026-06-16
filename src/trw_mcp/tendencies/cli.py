"""CLI entry point for the ``trw-mcp tendencies`` subcommand (PRD-QUAL-109 FR-03).

Lazy-dispatched from ``server/_subcommands.py`` to keep that facade under the
eLOC gate. Resolves the corpus roots (explicit ``--corpus`` or the config-driven
defaults), builds the advisory report, prints it, and ALWAYS exits 0 (advisory,
never auto-blocking; NFR-02). No artifact is mutated (NFR-04).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trw_mcp.tendencies.report import (
    build_report,
    default_corpus_roots,
    render_human,
    render_json,
)


def _resolve_roots(args: argparse.Namespace) -> list[Path]:
    """Resolve corpus roots: explicit ``--corpus`` wins; else config-driven defaults."""
    corpus_arg = getattr(args, "corpus", None)
    if corpus_arg:
        return [Path(corpus_arg).resolve()]
    from trw_mcp.models.config import TRWConfig

    config = TRWConfig()
    prds_relative_path = str(getattr(config, "prds_relative_path", "") or "")
    return default_corpus_roots(Path.cwd(), prds_relative_path=prds_relative_path)


def run_tendencies(args: argparse.Namespace) -> None:
    """Run the advisory tendency report and exit 0 (always)."""
    report = build_report(_resolve_roots(args))
    if getattr(args, "as_json", False):
        print(render_json(report))
    else:
        print(render_human(report))
    sys.exit(0)


__all__ = ["run_tendencies"]
