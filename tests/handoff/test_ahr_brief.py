"""Small-tier support in the handoff CLI: constraints carried by tool, a strict check, briefs and the
mechanical check of what helpers return.

Each test names the small-model failure it closes (2026-10-06 skill eval and the 2026-10-08 brainstorm with a
small-tier model and an independent architect): retyped constraints, unknowns read as clean, invented or
irrelevant citations, silent omissions, and a helper promoting its own claim to verified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.handoff._cli_support import _check, _fill, _git, _new, _run, repo  # noqa: F401
from trw_mcp.handoff import load

RULES = "Never run git stash in this tree.\nAsk the operator before choosing a rounding mode.\nKeep the launcher untouched.\n"


@pytest.fixture
def rules(repo: Path) -> Path:
    path = repo / "RULES.md"
    path.write_text(RULES, encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "totals.py").write_text("def compute_total(items):\n    return sum(items)\n", encoding="utf-8")
    (repo / "secret.txt").write_text("out of scope\n", encoding="utf-8")
    (repo / "src" / "blob.bin").write_bytes(b"\xff\xfe not text\n")
    outside = repo.parent / f"{repo.name}-outside.txt"
    outside.write_text("out of scope\n", encoding="utf-8")
    (repo / "src" / "leak.txt").symlink_to(outside)  # in scope by name, outside the repository by target
    (repo / "src" / "inner.txt").symlink_to(repo / "secret.txt")  # in scope by name, out of scope by target
    _git(repo, "add", "RULES.md", "src/totals.py", "secret.txt")
    _git(repo, "commit", "-qm", "rules")
    return path


def _sealed(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[Path, str]:
    path = _new(capsys, "--subject", "sweep", "--next-read", "a.txt", *argv)
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 0, out + err
    return path, out.strip()


# --- constraints carried by tool ---------------------------------------------------------------------


def test_constraints_are_carried_verbatim_with_their_source(rules: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(
        capsys, "--tier", "critical", "--subject", "s", "--next-read", "a.txt",
        "--constraint", "Publish only after the 24h soak ends.",
        "--constraint-from", "RULES.md#L1-L2",
    )  # fmt: skip
    quoted, sourced = load(path)["constraints"]
    assert quoted == "Publish only after the 24h soak ends."
    assert sourced["text"] == "Never run git stash in this tree.\nAsk the operator before choosing a rounding mode."
    assert sourced["source"]["uri"] == "file:RULES.md#L1-L2" and sourced["source"]["digest"].startswith("sha256:")


@pytest.mark.parametrize("spec", ["RULES.md", "RULES.md#L9", "RULES.md#L2-L1", "../outside.md#L1"])
def test_a_bad_constraint_range_is_refused(rules: Path, capsys: pytest.CaptureFixture[str], spec: str) -> None:
    code, _out, err = _run(capsys, "new", "--subject", "s", "--next-read", "a.txt", "--constraint-from", spec)
    assert code == 2 and "--constraint-from" in err


def test_check_tells_a_verbatim_constraint_from_a_reworded_one(rules: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(
        capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt", "--constraint-from", "RULES.md#L1"
    )
    doc = _fill(load(path))
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert _run(capsys, "seal", str(path))[0] == 0
    code, report = _check(capsys, path)
    assert code == 0 and report["constraints"] == [{"index": 0, "status": "match"}]

    reworded = _new(
        capsys, "--tier", "standard", "--subject", "s2", "--next-read", "a.txt", "--constraint-from", "RULES.md#L1"
    )
    doc = _fill(load(reworded))
    doc["constraints"][0]["text"] = "Avoid git stash where possible."  # the paraphrase a schema cannot see
    reworded.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    assert _run(capsys, "seal", str(reworded))[0] == 0
    code, report = _check(capsys, reworded)
    assert code == 1 and report["constraints"][0]["status"] == "mismatch"


def test_check_reports_a_constraint_source_that_changed(rules: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, _digest = _sealed(capsys, "--tier", "standard", "--constraint-from", "RULES.md#L3")
    rules.write_text(RULES.replace("untouched", "alone"), encoding="utf-8")
    _code, report = _check(capsys, path)
    assert report["constraints"][0]["status"] == "drift"


# --- strict check ------------------------------------------------------------------------------------


def test_strict_check_refuses_unknowns_that_the_default_accepts(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt", "--next-read", "https://e.x/y")
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, _err = _run(capsys, "seal", str(path))
    assert code == 0
    sealed = out.strip()
    assert _check(capsys, path, "--digest", sealed)[0] == 0, "an unfetched https pointer is clean by default"
    code, _out, err = _run(capsys, "check", str(path), "--digest", sealed, "--strict")
    assert code == 1 and "FINDINGS in pointers" in err and "(strict)" in err


def test_strict_check_needs_the_digest_and_passes_on_positive_evidence(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt")
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, _err = _run(capsys, "seal", str(path))
    assert code == 0
    code, _out, err = _run(capsys, "check", str(path), "--strict")
    assert code == 1 and "FINDINGS in digest" in err
    assert _run(capsys, "check", str(path), "--digest", out.strip(), "--strict")[0] == 0


# --- briefs ------------------------------------------------------------------------------------------

ITEMS = [
    {"id": "i1", "question": "Which function computes the total?", "must_contain": "compute_total"},
    {"id": "i2", "question": "Does anything in scope call git stash?"},
]


def _items(repo: Path, items: list[dict[str, Any]] | None = None) -> str:
    path = repo / "items.json"
    path.write_text(json.dumps(items or ITEMS), encoding="utf-8")
    return str(path)


def _draft(capsys: pytest.CaptureFixture[str]) -> Path:
    """A draft is enough for a brief: scope and constraints are mechanical, judgement fields stay unfilled."""
    return _new(
        capsys, "--subject", "sweep", "--next-read", "a.txt", "--path", "src/**",
        "--constraint-from", "RULES.md#L1-L3",
    )  # fmt: skip


def test_briefs_share_one_prefix_and_carry_constraints_unchanged(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(capsys, "brief", str(_draft(capsys)), "--items", _items(rules.parent))
    assert code == 0, err
    rendered = json.loads(out)
    first, second = rendered["briefs"]["i1"], rendered["briefs"]["i2"]
    prefix = first[: first.index("---\n") + 4]
    assert second.startswith(prefix), "everything shared comes first and is byte-identical"
    for line in RULES.splitlines():
        assert line in prefix
    assert "- src/**" in prefix and "Ledger" in prefix and "i1, i2" in prefix
    assert first.rstrip().endswith("Question: Which function computes the total?")
    assert "verified" in prefix and '"observed|inferred"' in prefix and "no code fence" in prefix


def test_a_brief_needs_an_exact_scope(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    draft = _new(capsys, "--subject", "sweep", "--next-read", "a.txt")
    code, _out, err = _run(capsys, "brief", str(draft), "--items", _items(repo))
    assert code == 2 and "objective.paths" in err


def test_a_brief_refuses_instead_of_truncating(rules: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.handoff import AhrInputError
    from trw_mcp.handoff._brief import load_items, render_briefs

    record = load(_draft(capsys))
    with pytest.raises(AhrInputError, match="never truncates"):
        render_briefs(record, load_items(ITEMS), today="2026-10-08", max_chars=200)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"id": "a", "question": "q"}, {"id": "a", "question": "q"}],
        [{"id": "a b", "question": "q"}],
        [{"id": "a", "question": "two\nlines"}],
        [{"id": "a", "question": "q", "extra": 1}],
    ],
)
def test_malformed_items_are_refused(repo: Path, capsys: pytest.CaptureFixture[str], items: list[Any]) -> None:
    draft = _new(capsys, "--subject", "sweep", "--next-read", "a.txt", "--path", "src/**")
    path = repo / "items.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    assert _run(capsys, "brief", str(draft), "--items", str(path))[0] == 2


# --- checking what helpers return --------------------------------------------------------------------


def _row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "item": "i1",
        "status": "answered",
        "answer": "compute_total in src/totals.py",
        "evidence": [{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):\r\n"}],
        "label": "observed",
    }
    return row | changes


def _outcomes(rules: Path, capsys: pytest.CaptureFixture[str], rows: Any, *, raw: str | None = None) -> tuple[int, Any]:
    results = rules.parent / "results.json"
    results.write_text(raw if raw is not None else json.dumps(rows), encoding="utf-8")
    code, out, _err = _run(
        capsys, "brief-check", str(_draft(capsys)), "--items", _items(rules.parent), "--results", str(results)
    )
    return code, json.loads(out)


def test_a_row_whose_cited_line_is_exact_is_citation_valid_and_never_verified(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    i2 = _row(
        item="i2", answer="no", evidence=[{"path": "src/totals.py", "line": 2, "text": "    return sum(items)\n"}]
    )
    code, report = _outcomes(rules, capsys, [_row(), i2])
    assert code == 0
    assert [o["outcome"] for o in report["items"]] == ["citation_valid", "citation_valid"]
    assert "verified" not in json.dumps(report["items"]) and "not a judgement" in report["note"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"evidence": [{"path": "src/totals.py", "line": 1, "text": "def compute_total(values):"}]}, "text_mismatch"),
        ({"evidence": [{"path": "src/totals.py", "line": 2, "text": "def compute_total(items):"}]}, "text_mismatch"),
        ({"evidence": [{"path": "src/totals.py", "line": 99, "text": "x"}]}, "line_out_of_range"),
        ({"evidence": [{"path": "secret.txt", "line": 1, "text": "out of scope"}]}, "path_out_of_scope"),
        ({"evidence": [{"path": "src/../secret.txt", "line": 1, "text": "out of scope"}]}, "path_out_of_scope"),
        ({"evidence": [{"path": "src/nope.py", "line": 1, "text": "x"}]}, "path_missing"),
        ({"evidence": [{"path": "src/totals.py", "line": 2, "text": "    return sum(items)"}]}, "hint_not_matched"),
        ({"evidence": [{"path": "src/totals.py", "line": 2, "text": "return sum(items)"}]}, "text_mismatch"),
        ({"evidence": [{"path": "src/totals.py", "line": 1, "text": " def compute_total(items): "}]}, "text_mismatch"),
        ({"evidence": [{"path": "src/totals.py", "line": 1, "text": "   "}]}, "text_mismatch"),
        (
            {"evidence": [{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):\n\n"}]},
            "text_mismatch",
        ),
        ({"status": []}, "malformed_row"),
        ({"status": {"answered": True}}, "malformed_row"),
        ({"label": ["observed"]}, "label_not_allowed"),
        (
            {"evidence": [{"path": "src/totals.py", "line": True, "text": "def compute_total(items):"}]},
            "malformed_evidence",
        ),
        (
            {"evidence": [{"path": "src/totals.py", "line": 1.0, "text": "def compute_total(items):"}]},
            "malformed_evidence",
        ),
        (
            {"evidence": [{"path": "src/totals.py", "line": "1", "text": "def compute_total(items):"}]},
            "malformed_evidence",
        ),
        ({"evidence": ["src/totals.py:1"]}, "malformed_evidence"),
        ({"evidence": [{"path": "/etc/hosts", "line": 1, "text": "x"}]}, "path_out_of_scope"),
        ({"evidence": [{"path": "src/blob.bin", "line": 1, "text": "\ufffd"}]}, "path_unreadable"),
        ({"evidence": [{"path": "src/leak.txt", "line": 1, "text": "out of scope"}]}, "path_out_of_scope"),
        ({"evidence": [{"path": "src/inner.txt", "line": 1, "text": "out of scope"}]}, "path_out_of_scope"),
        ({"evidence": [{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):"}] * 11}, "no_evidence"),
        ({"answer": "  "}, "no_answer"),
        ({"evidence": []}, "no_evidence"),
        ({"label": "verified"}, "label_not_allowed"),
        ({"status": "inconclusive", "evidence": []}, "helper_inconclusive"),
        ({"status": "not_answered"}, "helper_not_answered"),
        ({"status": "done"}, "malformed_row"),
    ],
)
def test_a_row_that_fails_a_check_is_inconclusive(
    rules: Path, capsys: pytest.CaptureFixture[str], change: dict[str, Any], reason: str
) -> None:
    code, report = _outcomes(rules, capsys, [_row(**change)])
    first = report["items"][0]
    assert code == 1 and first["outcome"] == "inconclusive" and reason in first["reasons"]


def test_missing_and_duplicate_rows_are_inconclusive_never_clean(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, report = _outcomes(rules, capsys, [_row(), _row(), {"item": "zz", "status": "answered"}])
    by_id = {o["id"]: o for o in report["items"]}
    assert code == 1
    assert by_id["i1"]["reasons"] == ["duplicate_rows"] and by_id["i2"]["reasons"] == ["missing_row"]
    assert report["counts"] == {"citation_valid": 0, "inconclusive": 2, "unassigned_rows": 1}


def test_a_row_for_an_unknown_item_fails_the_check_even_when_every_item_is_valid(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A helper that answers an item nobody asked is drift the lead should see, not noise to drop."""
    i2 = _row(item="i2", evidence=[{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):"}])
    code, report = _outcomes(rules, capsys, [_row(), i2, _row(item="i3")])
    assert report["counts"] == {"citation_valid": 2, "inconclusive": 0, "unassigned_rows": 1}
    assert code == 1


def test_rows_may_arrive_one_json_object_per_line_and_garbage_is_not_an_answer(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = json.dumps(_row()) + "\nI could not finish this one, sorry.\n"
    code, report = _outcomes(rules, capsys, None, raw=raw)
    by_id = {o["id"]: o["outcome"] for o in report["items"]}
    assert code == 1 and by_id == {"i1": "citation_valid", "i2": "inconclusive"}


def test_rows_are_read_from_fenced_pretty_printed_and_concatenated_helper_output(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What helpers actually return (3 of 8 fenced their object in the first sweep) must check without hand editing."""
    i2 = _row(item="i2", evidence=[{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):"}])
    raw = "```json\n" + json.dumps(_row()) + "\n```\n\n" + json.dumps(i2, indent=2) + "\n"
    code, report = _outcomes(rules, capsys, None, raw=raw)
    assert code == 0 and report["counts"] == {"citation_valid": 2, "inconclusive": 0, "unassigned_rows": 0}

    prose = "Here is my answer:\n" + raw  # prose is not a row: the check fails, the real rows still count
    code, report = _outcomes(rules, capsys, None, raw=prose)
    assert code == 1 and report["counts"] == {"citation_valid": 2, "inconclusive": 0, "unassigned_rows": 1}


# --- hostile or broken inputs ------------------------------------------------------------------------


def test_a_constraint_source_that_is_not_text_is_never_a_match(repo: Path) -> None:
    """Replacement characters must not let a quote "match" bytes that do not decode."""
    import hashlib

    from trw_mcp.handoff._quotes import quote_status

    blob = repo / "blob.bin"
    blob.write_bytes(b"\xff\xfe")
    source = {"uri": "file:blob.bin", "digest": "sha256:" + hashlib.sha256(b"\xff\xfe").hexdigest()}
    status = quote_status("\ufffd\ufffd", source, repo)
    assert status["status"] == "not_accessed" and "UTF-8" in status["reason"]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"constraints": {"checked": "2026-10-08T00:00:00Z"}}, "neither a list"),
        ({"constraints": {"none_known": "yes"}}, "neither a list"),
        ({"constraints": "do not stash"}, "neither a list"),
        ({"constraints": None}, "neither a list"),
        ({"constraints": [{"text": "   "}]}, "unfilled"),
        ({"constraints": [{"source": {"uri": "file:RULES.md#L1"}}]}, "unfilled"),
        ({"objective": {"paths": []}}, "objective.paths"),
        ({"objective": "sweep"}, "objective.paths"),
    ],
)
def test_a_record_with_a_malformed_field_is_refused_not_guessed(
    rules: Path, capsys: pytest.CaptureFixture[str], patch: dict[str, Any], message: str
) -> None:
    from trw_mcp.handoff import AhrInputError
    from trw_mcp.handoff._brief import load_items, render_briefs

    record = load(_draft(capsys)) | patch
    with pytest.raises(AhrInputError, match=message):
        render_briefs(record, load_items(ITEMS), today="2026-10-08")


@pytest.mark.parametrize(
    "patch",
    [
        {"as_of": None},
        {"as_of": "yesterday"},
        {"as_of": {"base_ref": ["abc"]}},
        {"constraints": [{"text": "Keep the launcher untouched.", "source": None}]},
        {"constraints": [{"text": "Keep the launcher untouched.", "source": {"uri": 7}}]},
        {"constraints": {"none_known": True, "checked": "2026-10-08T00:00:00Z"}},
    ],
)
def test_a_record_with_an_odd_optional_field_still_renders(
    rules: Path, capsys: pytest.CaptureFixture[str], patch: dict[str, Any]
) -> None:
    from trw_mcp.handoff._brief import load_items, render_briefs

    brief = render_briefs(load(_draft(capsys)) | patch, load_items(ITEMS), today="2026-10-08")["briefs"]["i1"]
    if "as_of" in patch:
        assert "Repository at commit unknown." in brief
    elif isinstance(patch["constraints"], dict):
        assert "Constraints: none recorded." in brief
    else:
        assert "- Keep the launcher untouched.\n" in brief and "[source" not in brief


def test_item_text_is_bounded_so_a_brief_cannot_grow_past_its_cap(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.handoff._brief import MAX_ITEM_TEXT

    draft = _new(capsys, "--subject", "sweep", "--next-read", "a.txt", "--path", "src/**")
    for key in ("question", "hint", "must_contain"):
        item = {"id": "a", "question": "q"} | {key: "x" * (MAX_ITEM_TEXT + 1)}
        code, _out, err = _run(capsys, "brief", str(draft), "--items", _items(repo, [item]))
        assert code == 2 and key in err


def test_oversized_and_deeply_nested_inputs_are_input_errors_not_crashes(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.handoff._brief import MAX_ROWS

    repo, draft = rules.parent, str(_draft(capsys))
    results = repo / "results.json"
    deep = repo / "deep.json"
    deep.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    code, _out, err = _run(capsys, "brief", draft, "--items", str(deep))
    assert code == 2 and err.startswith("input error:")  # which message depends on the interpreter's JSON depth

    results.write_text(deep.read_text(encoding="utf-8"), encoding="utf-8")
    code, out, _err = _run(capsys, "brief-check", draft, "--items", _items(repo), "--results", str(results))
    assert code == 1 and json.loads(out)["counts"] == {"citation_valid": 0, "inconclusive": 2, "unassigned_rows": 1}

    results.write_text(json.dumps([_row()] * (MAX_ROWS + 1)), encoding="utf-8")
    code, _out, err = _run(capsys, "brief-check", draft, "--items", _items(repo), "--results", str(results))
    assert code == 2 and "at most" in err

    results.write_text("x" * (4 * 1024 * 1024 + 1), encoding="utf-8")
    code, _out, err = _run(capsys, "brief-check", draft, "--items", _items(repo), "--results", str(results))
    assert code == 2 and "cannot read results" in err

    big = repo / "big.json"
    big.write_text(json.dumps(ITEMS) + " " * (256 * 1024), encoding="utf-8")
    code, _out, err = _run(capsys, "brief", draft, "--items", str(big))
    assert code == 2 and "cannot read items" in err


def test_line_numbers_are_the_ones_an_editor_shows_even_after_a_form_feed(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``str.splitlines`` breaks at a form feed or U+2028 and would shift every later line by one."""
    repo = rules.parent
    (repo / "src" / "paged.py").write_text("A = 1\x0c\nB = 2\u2028\nC = 3\n", encoding="utf-8")
    cited = _row(item="i2", evidence=[{"path": "src/paged.py", "line": 3, "text": "C = 3"}])
    _code, report = _outcomes(rules, capsys, [cited])
    assert {o["id"]: o["outcome"] for o in report["items"]}["i2"] == "citation_valid"

    (repo / "PAGED.md").write_text("Intro\x0c\nNever force-push this branch.\n", encoding="utf-8")
    draft = _new(capsys, "--subject", "s", "--next-read", "a.txt", "--constraint-from", "PAGED.md#L2")
    assert load(draft)["constraints"][0]["text"] == "Never force-push this branch."


def test_cited_files_share_one_read_budget_for_the_whole_check(
    rules: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows are helper output: many rows naming many large files must not read without bound."""
    from trw_mcp.handoff import _brief

    record, items = load(_draft(capsys)), _brief.load_items(ITEMS)
    repo = rules.parent
    (repo / "src" / "other.py").write_text("X = 1\n", encoding="utf-8")
    rows = [_row(), _row(item="i2", evidence=[{"path": "src/other.py", "line": 1, "text": "X = 1"}])]
    monkeypatch.setattr(_brief, "_MAX_TOTAL_BYTES", 1)  # the first file spends it; a second is not opened
    by_id = {o["id"]: o for o in _brief.check_results(record, items, rows, repo)["items"]}
    assert by_id["i1"]["outcome"] == "citation_valid"
    assert by_id["i2"]["reasons"] == ["read_budget_exhausted"]


def test_scope_width_is_the_leads_choice_and_never_leaves_the_repository(
    rules: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.handoff._brief import check_results, load_items

    record = load(_draft(capsys)) | {"objective": {"paths": ["**", "/etc/**", "../**"]}}
    rows = [
        _row(evidence=[{"path": "src/totals.py", "line": 1, "text": "def compute_total(items):"}]),
        _row(
            item="i2",
            evidence=[
                {"path": "secret.txt", "line": 1, "text": "out of scope"},  # inside the repository: now in scope
                {"path": "src/leak.txt", "line": 1, "text": "out of scope"},  # a symlink out of the repository
                {"path": "/etc/hosts", "line": 1, "text": "x"},
                {"path": "../x", "line": 1, "text": "x"},
            ],
        ),
    ]
    by_id = {o["id"]: o for o in check_results(record, load_items(ITEMS), rows, rules.parent)["items"]}
    assert by_id["i1"]["outcome"] == "citation_valid"
    assert by_id["i2"]["reasons"] == ["path_out_of_scope"]

    wide = [rows[0], _row(item="i2", evidence=[{"path": "secret.txt", "line": 1, "text": "out of scope"}])]
    outcomes = check_results(record, load_items(ITEMS), wide, rules.parent)["items"]
    assert [o["outcome"] for o in outcomes] == ["citation_valid", "citation_valid"]


def test_a_symlink_loop_is_refused_with_a_reason_on_every_interpreter(
    rules: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Path.resolve`` raises RuntimeError on a loop before 3.13 and returns the path after; neither may crash."""
    from trw_mcp.handoff._repo import confined_path

    repo = rules.parent
    (repo / "src" / "loop.py").symlink_to(repo / "src" / "loop2.py")
    (repo / "src" / "loop2.py").symlink_to(repo / "src" / "loop.py")
    cited = _row(item="i2", evidence=[{"path": "src/loop.py", "line": 1, "text": "x"}])
    _code, report = _outcomes(rules, capsys, [_row(), cited])
    i2 = {o["id"]: o for o in report["items"]}["i2"]
    assert i2["outcome"] == "inconclusive" and set(i2["reasons"]) <= {"path_out_of_scope", "path_missing"}

    real_resolve = Path.resolve

    def old_resolve(self: Path, strict: bool = False) -> Path:
        if "loop" in self.name:
            raise RuntimeError(f"Symlink loop from {self}")
        return real_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", old_resolve)
    assert confined_path("file:src/loop.py", repo) == (None, "unresolvable path (symlink loop)")
    for flag in ("--constraint-from", "--next-read"):
        spec = "src/loop.py#L1" if flag == "--constraint-from" else "src/loop.py"
        code, _out, err = _run(capsys, "new", "--subject", "s", "--next-read", "a.txt", flag, spec)
        assert code == 2 and "does not resolve" in err


# --- layering ----------------------------------------------------------------------------------------


def test_the_handoff_package_stays_a_leaf() -> None:
    """Dispatch and workflows may call it; it imports nothing of theirs (PRD-CORE-360-NFR01)."""
    import re

    import trw_mcp.handoff as package

    imported: set[str] = set()
    for source in Path(package.__file__).parent.glob("*.py"):
        imported.update(
            re.findall(r"^\s*(?:from|import) (trw_mcp(?:\.\w+)*)", source.read_text(encoding="utf-8"), re.MULTILINE)
        )
    assert imported and all(name.startswith("trw_mcp.handoff") for name in imported), sorted(imported)
