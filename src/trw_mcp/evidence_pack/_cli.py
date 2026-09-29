"""``trw-mcp run evidence-pack RUN_PATH [--out FILE]`` (PRD-CORE-323 FR05).

Exit codes: 0 written, 2 refused with a named reason on stderr. The verb writes
one file and is therefore not on the bounded-lane read-only allowlist (NFR04).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from trw_mcp.evidence_pack._wording import LIMIT_LINES

_DESCRIPTION = "\n".join(
    (
        "Export one run's requirements, decisions, evidence and verdict as one deterministic,",
        "redacted canonical-JSON pack file. Reads the run's logs and receipts, its scoped PRDs,",
        "its override records and the delivery journal (read-only); writes only --out.",
        "",
        *LIMIT_LINES,
    )
)


def add_evidence_pack_parser(verbs: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``run evidence-pack``; the help text carries the three fixed lines verbatim.

    Runtime caller: ``tools._run_cli.add_run_subcommands`` while the CLI parser is built.
    """
    pack = verbs.add_parser(
        "evidence-pack",
        help="Export one run's evidence pack for readiness review; writes one file",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pack.add_argument("run_path", metavar="RUN_PATH", help="The run directory; must be inside the project root")
    pack.add_argument(
        "--out",
        default="evidence-pack.json",
        metavar="FILE",
        help="Output file (default: evidence-pack.json in the current directory)",
    )


def _write_atomically(target: Path, data: bytes) -> None:
    handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False)  # noqa: SIM115
    try:
        with handle:
            handle.write(data)
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def run_evidence_pack(args: argparse.Namespace) -> int:
    """Build the pack for ``args.run_path`` and write it to ``args.out``; return the exit code.

    Runtime caller: ``tools._run_cli.run_run`` (``SUBCOMMAND_HANDLERS["run"]``).
    A refusal prints ``refused: <reason>`` on stderr and writes nothing.
    """
    from trw_mcp.evidence_pack import PackRefusedError, build_pack

    try:
        data = build_pack(Path(str(args.run_path)))
    except PackRefusedError as exc:
        print(f"refused: {exc.reason}" + (f" ({exc.detail})" if exc.detail else ""), file=sys.stderr)
        return 2
    target = Path(str(args.out))
    try:
        _write_atomically(target, data)
    except OSError as exc:
        print(f"refused: output_unwritable ({exc.strerror or type(exc).__name__})", file=sys.stderr)
        return 2
    pack_digest = json.loads(data)["manifest"]["pack_digest"]
    print(f"written: {target}")
    print(f"bytes: {len(data)}")
    print(f"pack_digest: {pack_digest}")
    return 0
