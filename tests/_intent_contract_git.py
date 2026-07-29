"""Shared temp-git-repo scaffolding for the PRD-SEC-013 git-side control tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CONTRACT_PATH = ".trw/contracts/must-not-happen.yaml"
PROTECTED = "protected/module.py"

pytest_skip_no_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
pytest_skip_no_ssh_keygen = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen is not installed")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, shell=False)
    return result.stdout


def contract_yaml(
    *,
    state: str = "active",
    authority: str = "human_approved",
    channel: str = "blocking_hook",
    anchors: str = PROTECTED,
    falsifier: str = "tests/test_protected.py::test_guard",
    claim_id: str = "C-1",
) -> str:
    return (
        "contract_id: INTENT-TEST\n"
        "must_not_happen:\n"
        f"  - claim_id: {claim_id}\n"
        '    text: "the guard must not be removed"\n'
        f"    authority_class: {authority}\n"
        f"    state: {state}\n"
        "    machine_checkable: true\n"
        f"    binding_channel: {channel}\n"
        f'    anchors: ["{anchors}"]\n'
        "    falsifiers:\n"
        "      - kind: pytest\n"
        f'        node_id: "{falsifier}"\n'
    )


def write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "sec013@example.test")
    git(repo, "config", "user.name", "sec013")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def seed_contract_repo(tmp_path: Path, name: str = "repo") -> tuple[Path, str]:
    """A repo whose first commit carries the contract and the protected file."""
    repo = init_repo(tmp_path, name)
    write(repo, PROTECTED, "def guard():\n    return True\n")
    write(repo, CONTRACT_PATH, contract_yaml())
    return repo, commit_all(repo, "seed contract and protected module")


def enable_ssh_signing(repo: Path) -> str:
    """Configure real ssh commit signing; returns the allowed-signers path."""
    key = repo / ".sign-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
        shell=False,
    )
    signers = repo / ".allowed-signers"
    signers.write_text(f"sec013@example.test {(key.with_suffix('.pub')).read_text().strip()}\n", encoding="utf-8")
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", str(key))
    git(repo, "config", "gpg.ssh.allowedSignersFile", str(signers))
    return str(signers)
