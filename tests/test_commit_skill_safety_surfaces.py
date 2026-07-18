"""Concurrency and evidence-binding contracts for commit skill variants."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills" / "trw-commit" / "SKILL.md",
    ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "codex" / "skills" / "trw-commit" / "SKILL.md",
    ROOT / ".claude" / "skills" / "trw-commit" / "SKILL.md",
    ROOT / ".agents" / "skills" / "trw-commit" / "SKILL.md",
)


def test_commit_variants_are_pin_aware_and_exactly_staged() -> None:
    for path in PATHS:
        content = path.read_text(encoding="utf-8")
        for phrase in (
            "active run returned for this MCP session",
            "{RUN_ROOT}/meta/run.yaml",
            # PRD-CORE-219 P09: candidate-first is the numbered procedure —
            # claim exact paths, review the owned diff, publish via the CLI.
            "Claim exact paths only",
            "Do NOT `git add`",
            "git diff HEAD -- <owned-paths>",
            "Abort on any unexpected content",
            "bound to this pinned run/change set",
            "trw-mcp prepare-candidate",
            "trw-mcp commit-candidate",
            "--transaction-id <prepared-transaction-id>",
            "Every current",
            "mixed-ownership file",
            "native-integration step under verified repository quiescence",
        ):
            assert phrase in content, f"{path}: missing {phrase!r}"
        for forbidden in (
            ".trw/context/run.yaml",
            "trw-{prd-id}-{role}",
            "find the active run directory",
            ".trw/context/build-status.yaml` dependency",
            "commit-candidate --path",
            "--run-id <run-id>",
        ):
            assert forbidden not in content, f"{path}: retains {forbidden!r}"


def test_prd_qual_119_fr07() -> None:
    """FR07 acceptance: Given another worker changes files, When completion and
    commit guidance run, Then criteria remain unchanged and the operation
    isolates or fails with ownership conflict — asserted across every mirror."""
    for path in PATHS:
        content = path.read_text(encoding="utf-8")
        # Concurrency contract: acceptance is never weakened by concurrency.
        for phrase in (
            "Concurrency contract (PRD-QUAL-119-FR07)",
            "never changes what done means",
            "acceptance criteria, test scope, review depth, and evidence",
            "isolate the owned change set or fail with an ownership conflict",
            "never shrink verification, drop assertions, or downgrade evidence",
        ):
            assert phrase in content, f"{path}: missing {phrase!r}"
        # Isolation mechanics remain intact (ownership conflict is a real outcome).
        assert "stop rather than absorbing another" in content
        assert "mixed-ownership file" in content


# ---------------------------------------------------------------------------
# PRD-CORE-219 FR03/FR04/FR07
# ---------------------------------------------------------------------------


def _txn_fixture(tmp_path):
    import hashlib
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "owned.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    parent = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "owned.py").write_text("v2\n", encoding="utf-8")

    from trw_mcp.models.git_commit_transaction import OwnershipManifest

    def manifest(txn: str = "txn-r") -> OwnershipManifest:
        return OwnershipManifest(
            transaction_id=txn,
            run_id="run-r",
            parent_oid=parent,
            owned_paths=("owned.py",),
            path_digests={"owned.py": "sha256:" + hashlib.sha256((repo / "owned.py").read_bytes()).hexdigest()},
        )

    return repo, parent, manifest


def test_prd_core_219_fr03(tmp_path) -> None:
    """FR03 acceptance: Given unexpected path, changed post-edit digest, parent
    change, or message change, When review validates, Then candidate
    publication blocks until a new diff and evidence review are recorded."""
    import pytest

    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        record_candidate_review,
    )

    repo, parent, manifest = _txn_fixture(tmp_path)

    # No review at all -> blocked.
    with pytest.raises(GitTransactionError, match="no current review"):
        publish_reviewed_candidate(repo, manifest(), "feat: msg")

    # Post-review content edit -> blocked until re-review.
    reviewed = manifest()
    record_candidate_review(repo, reviewed, "feat: msg")
    (repo / "owned.py").write_text("v3-tampered\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="changed since"):
        # Blocks via the content binding (ownership digest) — the reviewed
        # manifest no longer matches the tampered bytes.
        publish_reviewed_candidate(repo, reviewed, "feat: msg")

    # Message change after review -> blocked.
    fresh = manifest("txn-m")
    record_candidate_review(repo, fresh, "feat: reviewed message")
    with pytest.raises(GitTransactionError, match="message changed"):
        publish_reviewed_candidate(repo, fresh, "feat: DIFFERENT message")

    # A new complete review publishes cleanly.
    final = manifest("txn-ok")
    record_candidate_review(repo, final, "feat: final")
    journal = publish_reviewed_candidate(repo, final, "feat: final")
    assert journal.candidate_oid


def test_prd_core_219_fr04(tmp_path) -> None:
    """FR04 acceptance: failing hooks block; a hook that MUTATES the candidate
    context invalidates the review (back to prepared, re-review required); a
    clean run publishes; required-signature failure blocks publication."""
    import os

    import pytest

    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        read_journal,
        record_candidate_review,
    )

    repo, parent, manifest = _txn_fixture(tmp_path)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)

    # Failing blocking hook -> typed block.
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    failing = manifest("txn-hookfail")
    record_candidate_review(repo, failing, "feat: hooks")
    with pytest.raises(GitTransactionError, match="pre-commit hook failed"):
        publish_reviewed_candidate(repo, failing, "feat: hooks")

    # Mutating hook -> review invalidated, journal back to prepared.
    hook.write_text("#!/bin/sh\necho mutated >> owned.py\nexit 0\n", encoding="utf-8")
    mutating = manifest("txn-hookmut")
    record_candidate_review(repo, mutating, "feat: hooks")
    with pytest.raises(GitTransactionError, match="mutated the candidate context"):
        publish_reviewed_candidate(repo, mutating, "feat: hooks")
    journal = read_journal(repo, "txn-hookmut")
    assert journal is not None and str(journal.state) == TransactionState.PREPARED.value
    # Restore content for the next case (the mutation appended a line).
    (repo / "owned.py").write_text("v2\n", encoding="utf-8")

    # Clean hook -> publishes.
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    clean = manifest("txn-hookok")
    record_candidate_review(repo, clean, "feat: hooks ok")
    assert publish_reviewed_candidate(repo, clean, "feat: hooks ok").candidate_oid

    # Required signature on an unsigned candidate -> blocked with typed reason.
    unsigned = manifest("txn-sig")
    record_candidate_review(repo, unsigned, "feat: signed")
    with pytest.raises(GitTransactionError, match="signature did not verify"):
        publish_reviewed_candidate(repo, unsigned, "feat: signed", require_signature=True)


def test_prd_core_219_fr04_hooks_cannot_stage_or_mutate_unowned_shared_paths(tmp_path: Path) -> None:
    """Adversarial hook writes/stages ``foreign.txt`` only in its disposable
    candidate context, invalidates review, and leaves every shared dimension
    byte-for-byte and semantically unchanged.

    This reproduces the independent-review failure where the same hook ran in
    the shared checkout, changed the real index and foreign bytes, and still
    published a candidate.
    """
    import os
    import subprocess

    import pytest

    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        read_journal,
        record_candidate_review,
    )

    repo, _, manifest = _txn_fixture(tmp_path)
    foreign = repo / "foreign.txt"
    foreign.write_text("other-worker-bytes\n", encoding="utf-8")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\nprintf 'hook-mutated\\n' > foreign.txt\ngit add -- foreign.txt\n",
        encoding="utf-8",
    )
    os.chmod(hook, 0o755)

    reviewed = manifest("txn-hook-foreign")
    record_candidate_review(repo, reviewed, "feat: isolated hooks")

    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    index_path = Path(git("rev-parse", "--path-format=absolute", "--git-path", "index").stdout.strip())
    before_head = git("rev-parse", "HEAD").stdout
    before_index_bytes = index_path.read_bytes()
    before_index_tree = git("write-tree").stdout
    before_porcelain = git("--no-optional-locks", "status", "--porcelain=v2", "--untracked-files=all").stdout
    before_foreign = foreign.read_bytes()

    with pytest.raises(GitTransactionError, match="mutated the candidate context"):
        publish_reviewed_candidate(repo, reviewed, "feat: isolated hooks")

    assert git("rev-parse", "HEAD").stdout == before_head
    assert index_path.read_bytes() == before_index_bytes
    assert git("write-tree").stdout == before_index_tree
    assert git("--no-optional-locks", "status", "--porcelain=v2", "--untracked-files=all").stdout == before_porcelain
    assert foreign.read_bytes() == before_foreign
    assert git("show-ref", "--verify", "--quiet", reviewed.candidate_ref(), check=False).returncode == 1
    journal = read_journal(repo, reviewed.transaction_id)
    assert journal is not None and str(journal.state) == TransactionState.PREPARED.value


def test_prd_core_219_fr04_hooks_cannot_mutate_shared_refs(tmp_path: Path) -> None:
    """A hook may rewrite branches/tags in its disposable Git directory, but
    the mutation blocks publication and the complete shared ref namespace stays
    unchanged.
    """
    import os
    import subprocess

    import pytest

    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        read_journal,
        record_candidate_review,
    )
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo = _txn_repo_for_surfaces(tmp_path)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "master"], check=True, capture_output=True)
    (repo / "anchor.txt").write_text("second parent\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "anchor.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "second parent"],
        check=True,
        capture_output=True,
    )
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")

    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "original=$(git rev-parse HEAD)\n"
        "git update-ref refs/heads/master HEAD^\n"
        'git update-ref refs/heads/hook-side "$original"\n'
        'git update-ref refs/tags/hook-created "$original^"\n',
        encoding="utf-8",
    )
    os.chmod(hook, 0o755)

    reviewed = build_manifest_from_worktree(
        repo,
        ("owned.py",),
        "run/hook-refs",
        transaction_id="txn-hook-refs",
    )
    record_candidate_review(repo, reviewed, "feat: isolated hook refs")

    def refs() -> dict[str, str]:
        result = subprocess.run(
            ["git", "-C", str(repo), "for-each-ref", "--format=%(refname) %(objectname)"],
            check=True,
            capture_output=True,
            text=True,
        )
        return dict(line.split(" ", 1) for line in result.stdout.splitlines())

    before_refs = refs()
    with pytest.raises(GitTransactionError, match="mutated the candidate context"):
        publish_reviewed_candidate(repo, reviewed, "feat: isolated hook refs")

    assert refs() == before_refs
    assert "refs/heads/hook-side" not in before_refs
    assert "refs/tags/hook-created" not in before_refs
    assert reviewed.candidate_ref() not in before_refs
    journal = read_journal(repo, reviewed.transaction_id)
    assert journal is not None and str(journal.state) == TransactionState.PREPARED.value


def test_prd_core_219_fr07() -> None:
    """FR07 acceptance (doctrine surface): every trw-commit mirror instructs the
    candidate-first workflow — publish a candidate ref, return typed handoff,
    never integrate the shared branch automatically."""
    for path in PATHS:
        content = path.read_text(encoding="utf-8")
        for phrase in (
            "candidate-first",
            "prepare-candidate",
            "post-claim build receipt",
            "refs/trw/commit-candidates",
            "integrated=false",
            "native-integration",
            "never integrates the checked-out branch or shared index",
        ):
            assert phrase in content, f"{path}: missing {phrase!r}"


def test_prd_core_219_nfr04(tmp_path: Path) -> None:
    """NFR04 acceptance: journals and claims persist with restrictive 0600
    modes and the hook message tempfile never survives the transaction."""
    import glob
    import stat as stat_module
    import tempfile

    from tests._git_commit_workflow_support import verified_candidate
    from trw_mcp.state.git_commit_transaction import record_candidate_review
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo = _txn_repo_for_surfaces(tmp_path)
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    # Claim mode is asserted IN FLIGHT (claims release at handoff).
    manifest = build_manifest_from_worktree(repo, ("owned.py",), "run/one", transaction_id="txn-perm")
    record_candidate_review(repo, manifest, "feat: modes\n")
    claim = repo / ".trw" / "git-transactions" / "claims" / "txn-perm.json"
    assert claim.is_file()
    assert stat_module.S_IMODE(claim.stat().st_mode) == 0o600
    from trw_mcp.state.git_commit_transaction import release_claim

    release_claim(repo, "txn-perm")

    result, _run_dir = verified_candidate(
        repo, ("owned.py",), "feat: modes\n", "run/one", transaction_id="txn-perm-publish"
    )
    journal = repo / ".trw" / "git-transactions" / "journals" / "txn-perm-publish.json"
    assert journal.is_file()
    assert stat_module.S_IMODE(journal.stat().st_mode) == 0o600
    # The hook commit-message tempfile is cleaned up in the finally block.
    leftovers = glob.glob(str(Path(tempfile.gettempdir()) / "*.msg"))
    assert not any(Path(p).stat().st_size and "feat: modes" in Path(p).read_text() for p in leftovers)
    assert result["candidate_oid"]


def _txn_repo_for_surfaces(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "owned.py").write_text("owned-v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    return repo


def test_fr03_unexpected_path_and_isolated_parent_change_block(tmp_path: Path) -> None:
    """Audit F8: the two FR03 triggers without direct evidence — an unexpected
    path added after review, and an isolated parent change with content held
    constant — each block publication until a new review is recorded."""
    import subprocess

    import pytest

    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        record_candidate_review,
    )
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo = _txn_repo_for_surfaces(tmp_path)
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")

    # Unexpected path: reviewed one path, publish attempts two.
    reviewed = build_manifest_from_worktree(repo, ("owned.py",), "run/one", transaction_id="txn-f8a")
    record_candidate_review(repo, reviewed, "msg")
    (repo / "extra.py").write_text("extra\n", encoding="utf-8")
    widened = build_manifest_from_worktree(repo, ("owned.py", "extra.py"), "run/one", transaction_id="txn-f8a")
    with pytest.raises(GitTransactionError, match="content changed since review"):
        publish_reviewed_candidate(repo, widened, "msg")

    # Isolated parent change: same content claim, branch tip moved.
    fresh = build_manifest_from_worktree(repo, ("owned.py",), "run/one", transaction_id="txn-f8b")
    record_candidate_review(repo, fresh, "msg")
    (repo / "mover.py").write_text("move\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "mover.py"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "mover", "--", "mover.py"],
        check=True,
        capture_output=True,
    )
    moved_parent = fresh.model_copy(update={"parent_oid": "0" * 40})
    with pytest.raises(GitTransactionError, match=r"changed since review|reviewed parent changed"):
        publish_reviewed_candidate(repo, moved_parent, "msg")


def test_fr07_doctrine_routes_through_candidate_first() -> None:
    """Audit F3: the numbered workflow itself must route through the
    candidate-first entrypoint — phrase presence is not enough. Step 6 must
    not instruct shared-index staging, and step 9 must name the CLI."""
    root = Path(__file__).resolve().parents[2]
    mirrors = [
        root / ".agents" / "skills" / "trw-commit" / "SKILL.md",
        root / ".claude" / "skills" / "trw-commit" / "SKILL.md",
        root / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills" / "trw-commit" / "SKILL.md",
        root / "trw-mcp" / "src" / "trw_mcp" / "data" / "codex" / "skills" / "trw-commit" / "SKILL.md",
    ]
    for mirror in mirrors:
        content = mirror.read_text(encoding="utf-8")
        workflow = content.split("## Concurrency contract")[0]
        # The numbered procedure must publish via the production entrypoint...
        assert "trw-mcp commit-candidate" in workflow, mirror
        assert "trw-mcp prepare-candidate" in workflow, mirror
        assert "--transaction-id <prepared-transaction-id>" in workflow, mirror
        # ...and must NOT instruct shared-index staging as the default step.
        assert "git add -- <path" not in workflow, mirror
        assert "Do NOT `git add`" in workflow, mirror
        # Direct path-limited commit survives ONLY as the quiesced later step.
        assert "native-integration step under verified repository quiescence" in workflow, mirror


def test_fr04_valid_signature_publishes(tmp_path: Path) -> None:
    """Audit F5: require_signature=True must be SATISFIABLE — the candidate is
    signed at creation and verify-commit passes with a valid key."""
    import os
    import shutil
    import subprocess

    import pytest

    if shutil.which("gpg") is None:
        pytest.skip("gpg unavailable")

    gnupg_home = tmp_path / "gnupg"
    gnupg_home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(gnupg_home)}
    keygen = subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-gen-key",
            "trw-test <trw@test>",
            "default",
            "default",
            "never",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if keygen.returncode != 0:
        pytest.skip(f"ephemeral gpg key unavailable: {keygen.stderr[:120]}")

    repo = _txn_repo_for_surfaces(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.signingkey", "trw@test"],
        check=True,
        capture_output=True,
    )
    (repo / "owned.py").write_text("signed\n", encoding="utf-8")

    from tests._git_commit_workflow_support import verified_candidate

    original_environ = dict(os.environ)
    os.environ["GNUPGHOME"] = str(gnupg_home)
    try:
        result, _run_dir = verified_candidate(repo, ("owned.py",), "feat: signed\n", "run/one", require_signature=True)
    finally:
        os.environ.clear()
        os.environ.update(original_environ)
    verify = subprocess.run(
        ["git", "-C", str(repo), "verify-commit", result["candidate_oid"]],
        capture_output=True,
        text=True,
        env=env,
    )
    assert verify.returncode == 0, verify.stderr
