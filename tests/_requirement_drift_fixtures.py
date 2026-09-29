"""Git-fixture helpers shared by the PRD-CORE-321 Slice 2 tests.

A throwaway repository whose commits carry fixed identities and increasing
dates, plus a PRD text builder whose ``verification.mappings`` and traceability
``Call chain`` cells are chosen per test. Used by
``tests/test_requirement_drift.py`` and ``tests/test_requirement_drift_deliver.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TypedDict

PRDS = "docs/requirements-aare-f/prds"

_ISOLATED_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.invalid",
}


class Mapping(TypedDict):
    id: str
    criteria: list[str]
    evidence: str


def prd_text(
    prd_id: str,
    status: str,
    mappings: list[Mapping | str],
    *,
    chains: dict[str, str] | None = None,
    extra: str = "",
) -> str:
    """A PRD whose mappings are built from *mappings*; a ``str`` item is inserted as raw YAML.

    An empty *mappings* list writes ``mappings: []`` (an approved PRD with no mappings).
    *chains* maps a traceability key (``FR01``) to its raw ``Call chain`` cell.
    """
    lines = ["---", "prd:", f"  id: {prd_id}", f"  status: {status}"]
    if extra:
        lines.append(extra.rstrip("\n"))
    lines += ["  verification:", "    mappings: []" if not mappings else "    mappings:"]
    for mapping in mappings:
        if isinstance(mapping, str):
            lines.append(mapping.rstrip("\n"))
            continue
        lines.append(f"      - requirement_id: {mapping['id']}")
        lines.append("        acceptance_criteria:")
        lines += [f"          - {json.dumps(item)}" for item in mapping["criteria"]]
        lines.append(f"        evidence_artifact: {json.dumps(mapping['evidence'])}")
    lines += ["---", f"# {prd_id}", ""]
    if chains:
        lines += ["| Requirement | Source | Call chain |", "|-------------|--------|------------|"]
        lines += [f"| {key} | US-1 | {cell} |" for key, cell in chains.items()]
    return "\n".join(lines) + "\n"


class Repo:
    """A throwaway git repository rooted at *root* with a PRD directory at :data:`PRDS`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.day = 0
        (root / PRDS).mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(_ISOLATED_ENV)
        stamp = f"2026-01-{self.day + 1:02d}T12:00:00+00:00"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
        result = subprocess.run(["git", *args], cwd=self.root, env=env, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def write(self, name: str, text: str) -> Path:
        path = self.root / PRDS / name
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, message: str) -> str:
        self.git("add", "--all", "--", PRDS)
        self.git("commit", "-q", "--no-gpg-sign", "-m", message, "--", PRDS)
        self.day += 1
        return self.git("rev-parse", "HEAD")
