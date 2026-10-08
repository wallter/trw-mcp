"""``trw-mcp handoff new`` (draft scaffold), ``check`` (receiver pre-flight) and the ``placeholder`` finding.

Every test drives the real parser and handler table against a scratch git repository.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.handoff._cli_support import _check, _fill, _filled_sealed, _git, _new, _raw, _run, repo  # noqa: F401
from tests.handoff._vectors import STANDARD
from trw_mcp.handoff import digest, load, validate
from trw_mcp.handoff._scaffold import sender_id
from trw_mcp.handoff._validate import PLACEHOLDER
from trw_mcp.server._cli_argparse import _build_arg_parser

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

# --- new -------------------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["minimal", "standard", "critical"])
def test_new_draft_fails_only_on_placeholders(repo: Path, capsys: pytest.CaptureFixture[str], tier: str) -> None:
    path = _new(capsys, "--tier", tier, "--subject", "swap-refusal", "--next-read", "a.txt")
    doc = load(path)
    findings = validate(
        doc if tier == "minimal" else {**doc, "integrity": {"canonicalization": "RFC8785", "digest": digest(doc)}}
    )
    assert findings and {f.rule for f in findings} == {"placeholder"}
    assert doc["as_of"]["base_ref"] == {
        "commit": _git(repo, "rev-parse", "HEAD").strip(),
        "branch": _git(repo, "symbolic-ref", "--short", "HEAD").strip(),
        "tree_state": "clean",
    }
    assert doc["created_at"] == doc["as_of"]["at"] and doc["created_at"].endswith("Z")
    assert doc.get("readback", {}).get("required", False) is (tier != "minimal")


@pytest.mark.parametrize("tier", ["minimal", "standard", "critical"])
def test_filled_draft_validates_and_seals(repo: Path, capsys: pytest.CaptureFixture[str], tier: str) -> None:
    path, sealed = _filled_sealed(capsys, "--tier", tier, "--next-read", "a.txt")
    assert _run(capsys, "validate", str(path))[0] == 0
    assert sealed == digest(load(path))


def test_standard_draft_with_placeholders_is_refused_by_seal(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt")
    before = path.read_bytes()
    code, out, _ = _run(capsys, "seal", str(path))
    assert code == 1 and {json.loads(line)["rule"] for line in out.splitlines()} == {"placeholder"}
    assert path.read_bytes() == before


def test_next_read_digest_is_raw_bytes_not_jcs(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = repo / "prior.json"
    shutil.copy(STANDARD, record)
    doc = load(_new(capsys, "--subject", "s", "--next-read", "prior.json", "--next-read", "https://example.com/x"))
    ptr, url = doc["next_read"]
    assert ptr["uri"] == "file:prior.json" and ptr["digest"] == _raw(record)
    assert ptr["digest"] != digest(load(record))
    assert url == {"uri": "https://example.com/x", "why": url["why"], "kind": "url"}


def test_dirty_tree_writes_changed_paths_sidecar(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    path = _new(capsys, "--tier", "standard", "--subject", "s")
    ref = load(path)["as_of"]["base_ref"]
    assert ref["tree_state"] == "dirty"
    sidecar = path.with_name(path.stem + ".changed-paths.txt")
    assert ref["changed_paths"]["uri"] == "file:" + sidecar.relative_to(repo).as_posix()
    assert ref["changed_paths"]["digest"] == _raw(sidecar)
    assert sidecar.read_text(encoding="utf-8").splitlines() == ["a.txt", "new.txt"]  # paths, no XY code


def test_output_path_resolution(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback = _new(capsys, "--subject", "s")
    assert fallback.parent == repo / ".trw" / "handoffs" and fallback.name == load(fallback)["handoff_id"] + ".json"
    run = repo / ".trw" / "runs" / "task" / "r1"
    monkeypatch.setattr("trw_mcp.state._paths.find_active_run", lambda **_: run)
    assert _new(capsys, "--subject", "s").parent == run / "handoffs"
    explicit = _new(capsys, "--subject", "s", "--out", "out/draft.json")
    assert explicit == repo / "out" / "draft.json"
    code, _, err = _run(capsys, "new", "--subject", "s", "--out", "out/draft.json")
    assert code == 2 and "never overwritten" in err


def test_bad_subject_is_a_usage_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, "new", "--subject", "has space")[0] == 2


def test_outside_git_tree_state_is_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    out = _new(capsys, "--subject", "s", "--out", str(plain / "d.json"))
    assert load(out)["as_of"]["base_ref"] == {"tree_state": "unknown"}


@pytest.mark.parametrize(
    ("env", "prefix", "exact"),
    [
        ({"CLAUDE_CODE_SESSION_ID": "01984b86-aab2"}, "claude-code:", "claude-code:01984b86-aab2"),
        ({"CLAUDECODE": "1"}, "claude-code:", None),
        ({"CODEX_THREAD_ID": "th_9"}, "codex:", "codex:th_9"),
        ({"CODEX_CLI_VERSION": "1.0"}, "codex:", None),
        ({"CLAUDE_CODE_SESSION_ID": "a@b c"}, "claude-code:", "claude-code:abc"),
        ({}, "agent:", None),
    ],
)
def test_sender_id(env: dict[str, str], prefix: str, exact: str | None) -> None:
    got = sender_id(env)
    assert got.startswith(prefix) and "@" not in got
    if exact:
        assert got == exact


# --- placeholder finding ---------------------------------------------------------------------


def test_placeholder_finding_names_the_path_and_skips_extensions() -> None:
    doc = load(STANDARD)
    doc["not_done"][1] = f"decide later {PLACEHOLDER} what is left"  # matched anywhere in the string
    doc["extensions"]["https://example.org/ahr-ext/lane/1"]["lane"] = f"{PLACEHOLDER} opaque"
    findings = validate(doc)
    placeholders = [f for f in findings if f.rule == "placeholder"]
    assert [f.path for f in placeholders] == ["/not_done/1"]


def test_plain_todo_text_is_content_not_a_placeholder() -> None:
    """A third-party record or a verbatim constraint may say ``TODO:``; only the scaffold sentinel is a draft."""
    doc = load(STANDARD)
    doc["not_done"][1] = "TODO: decide whether to keep the shim"
    doc.pop("integrity", None)
    assert [f.rule for f in validate(doc) if f.rule == "placeholder"] == []


def test_enum_sentinels_are_placeholders_not_schema_noise(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``label``/``severity`` are never silently defaulted: the draft names them as placeholders."""
    doc = load(_new(capsys, "--subject", "s", "--next-read", "a.txt"))
    paths = {f.path for f in validate(doc) if f.rule == "placeholder"}
    assert {"/claims/0/label", "/risks/0/severity"} <= paths
    assert [f for f in validate(doc) if f.rule != "placeholder"] == []


def test_subject_is_required(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["handoff", "new"])


# --- check -----------------------------------------------------------------------------------


def test_check_clean_record_exits_0(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, sealed = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt", "--next-read", "https://e.x/y")
    code, report = _check(capsys, path, "--digest", sealed)
    assert code == 0
    assert report["digest"]["status"] == "match" and report["validity"]["valid"]
    assert report["pointer_checks"] == [
        {"index": 0, "status": "match", "observed_digest": _raw(repo / "a.txt")},
        {"index": 1, "status": "not_accessed", "reason": "https URI: never fetched by check"},
    ]
    assert report["git"]["status"] == "match" and report["supersession"]["status"] == "current"
    assert report["expiry"]["status"] == "valid"


def test_check_reports_drift_and_missing(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "b.txt").write_text("beta\n", encoding="utf-8")
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt", "--next-read", "b.txt")
    (repo / "a.txt").write_text("edited\n", encoding="utf-8")
    (repo / "b.txt").unlink()
    code, report = _check(capsys, path)
    assert code == 1
    assert report["pointer_checks"] == [
        {"index": 0, "status": "drift", "observed_digest": _raw(repo / "a.txt")},
        {"index": 1, "status": "missing"},
    ]


def test_check_no_digest_pointer_is_not_a_finding(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    doc = load(path)
    del doc["next_read"][0]["digest"]
    del doc["integrity"]  # minimal may be unsealed; a stale seal would be an X-1 finding
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 0 and report["pointer_checks"] == [{"index": 0, "status": "no_digest"}]


def test_check_digest_mismatch(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    code, report = _check(capsys, path, "--digest", "sha256:" + "0" * 64)
    assert code == 1 and report["digest"]["status"] == "mismatch"


def test_check_malformed_digest_is_a_usage_error(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    assert _run(capsys, "check", str(path), "--digest", "abc")[0] == 2


def test_check_expired(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(capsys, "--subject", "s", "--next-read", "a.txt")
    doc = _fill(load(path)) | {"created_at": "2020-01-01T00:00:00Z", "expires_at": "2020-01-08T00:00:00Z"}
    doc["as_of"]["at"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and report["expiry"] == {"status": "expired", "expires_at": "2020-01-08T00:00:00Z"}


def test_check_base_ref_drift(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and report["git"]["current"]["tree_state"] == "dirty" and report["git"]["status"] == "drift"


def test_new_commit_alone_is_information_not_drift(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "next")
    code, report = _check(capsys, path)
    assert code == 0 and report["git"]["status"] == "match" and report["git"]["commits_since"] == 1


def test_abbreviated_recorded_commit_is_a_finding(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    doc = load(path)
    doc["as_of"]["base_ref"]["commit"] = doc["as_of"]["base_ref"]["commit"][:12]
    del doc["integrity"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and report["git"]["status"] == "abbreviated_commit"


def test_recorded_commit_not_in_history_is_diverged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    doc = load(path)
    doc["as_of"]["base_ref"]["commit"] = "1" * 40
    del doc["integrity"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and report["git"]["status"] == "diverged"


def test_dirty_record_compares_changed_paths_not_status_codes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The record's own files never count, other files beside it do, and an M->MM change is not drift."""
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    path, _ = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt")
    _git(repo, "add", "a.txt")  # " M" becomes "M ": same path, same set
    path.with_name(path.stem + ".md").write_text("view\n", encoding="utf-8")  # the record's own render
    code, report = _check(capsys, path)
    assert report["git"]["changed_paths"] == "match" and report["git"]["status"] == "match", report["git"]
    (path.parent / "other-note.txt").write_text("x\n", encoding="utf-8")  # not the record's: it counts
    assert _check(capsys, path)[1]["git"]["changed_paths"] == "drift"


def test_tampered_changed_paths_sidecar_is_not_trusted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    path, _ = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt")
    sidecar = path.with_name(path.stem + ".changed-paths.txt")
    sidecar.write_text("a.txt\nb.txt\n", encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and report["git"]["changed_paths"] == "digest_mismatch"


def test_undecodable_sidecar_is_not_checked_not_exit_2(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    path, _ = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt")
    sidecar = path.with_name(path.stem + ".changed-paths.txt")
    sidecar.write_bytes(b"\xff\xfe\n")
    doc = load(path)
    doc["as_of"]["base_ref"]["changed_paths"]["digest"] = _raw(sidecar)
    doc.pop("integrity")
    doc["tier"] = "minimal"  # minimal may be unsealed: the edit must not trip X-1
    doc.pop("readback")
    doc["objective"].pop("intent")
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code in (0, 1) and report["git"]["changed_paths"] == "not_checked"


def test_check_never_dereferences_javascript_or_data(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _ = _filled_sealed(capsys, "--next-read", "a.txt")
    doc = load(path)
    doc["next_read"] += [{"uri": "javascript:alert(1)", "why": "x"}, {"uri": "data:text/plain,hi", "why": "y"}]
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, report = _check(capsys, path)
    assert code == 1 and [pc["status"] for pc in report["pointer_checks"][1:]] == ["not_accessed", "not_accessed"]
    assert {f["rule"] for f in report["validity"]["findings"]} >= {"R-SEC-2"}


def test_check_rejects_non_handoff_and_missing_file(
    repo: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    readback = Path(STANDARD).with_name("02-readback-for-01.json")
    assert _run(capsys, "check", str(readback))[0] == 2
    assert _run(capsys, "check", str(tmp_path / "absent.json"))[0] == 2


# --- rc.2: evidence skeleton, objective.paths, scope warning ---------------------------------------


def test_draft_claim_drafts_the_evidence_shape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The draft names every evidence key, so a `verified` claim is filled, not guessed (2026-10-06 eval)."""
    (claim,) = load(_new(capsys, "--subject", "s", "--next-read", "a.txt"))["claims"]
    (evidence,) = claim["evidence"]
    assert set(evidence) == {"procedure", "scope", "result", "at"} and evidence["result"] == "supports"
    assert "basis" in claim


def test_a_verified_claim_filled_from_the_draft_seals(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt")
    doc = load(path)
    claim = doc["claims"][0]
    del claim["basis"]
    claim |= {"text": "tests/test_a.py passes", "label": "verified"}
    claim["evidence"][0] |= {"procedure": "pytest tests/test_a.py -q", "scope": "one file", "at": doc["created_at"]}
    path.write_text(json.dumps(_fill(doc), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 0, out + err
    assert load(path)["claims"][0]["evidence"][0]["procedure"] == "pytest tests/test_a.py -q"


def test_paths_reach_the_draft_and_out_of_scope_changes_warn(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, sealed = _filled_sealed(
        capsys, "--tier", "standard", "--next-read", "a.txt", "--path", "src/**", "--path", "a.txt"
    )
    assert load(path)["objective"]["paths"] == ["src/**", "a.txt"]
    (repo / "src" / "deep").mkdir(parents=True)
    (repo / "src" / "deep" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "elsewhere.txt").write_text("not in scope\n", encoding="utf-8")
    _code, report = _check(capsys, path, "--digest", sealed)
    assert report["scope"] == {"status": "checked", "outside": ["elsewhere.txt"], "outside_count": 1}


def test_a_record_without_paths_has_no_scope_section(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, sealed = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt")
    (repo / "elsewhere.txt").write_text("x\n", encoding="utf-8")
    _code, report = _check(capsys, path, "--digest", sealed)
    assert "scope" not in report


def test_a_path_with_a_parent_segment_is_refused_at_seal(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt", "--path", "../outside")
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code != 0 and "objective/paths" in out + err
