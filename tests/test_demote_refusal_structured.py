"""DEMOTE-REFUSAL-STRUCTURED: update-project demotes a marker refusal by its kind, never by the message's text.

``demote_file_scoped_refusals`` moved any error whose TEXT contained ``ambiguous_markers`` or "TRW markers are
duplicated or unbalanced" to the warnings, so an unrelated failure whose PATH held either token (a project under
``~/ambiguous_markers/``) was demoted too, and update-project committed instead of rolling back. A refusal now carries
its kind (``FileScopedRefusal.kind``) from the site that refused, and only that kind is demoted.
"""

from __future__ import annotations

import copy
import pickle
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.state.claude_md._marker_layout import FileScopedRefusal, demote_file_scoped_refusals, refusal_message

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


def test_an_unrelated_failure_whose_path_holds_the_token_still_rolls_back() -> None:
    result = {
        "errors": [
            "Failed to write /home/me/ambiguous_markers/.claude/settings.json: [Errno 28] No space left on device",
            "Refused to write /srv/TRW markers are duplicated or unbalanced/AGENTS.md (shrink_floor): would drop 40",
        ],
        "warnings": [],
    }

    assert demote_file_scoped_refusals(result) == 0
    assert len(result["errors"]) == 2 and result["warnings"] == []


def test_a_refusal_of_the_file_scoped_kind_is_demoted_whatever_its_text() -> None:
    marker = refusal_message("Refused to write /p/AGENTS.md (ambiguous_markers): left as found", "ambiguous_markers")
    other = refusal_message("Refused to write /p/ambiguous_markers/CLAUDE.md (shrink_floor): drop 40", "shrink_floor")
    result = {"errors": [marker, other], "warnings": []}

    assert demote_file_scoped_refusals(result) == 1
    assert result["errors"] == [other] and result["warnings"] == [marker]


def test_the_kind_survives_the_copies_a_result_goes_through() -> None:
    marker = refusal_message("AGENTS.md left untouched: x", "ambiguous_markers")

    for clone in (copy.copy(marker), copy.deepcopy(marker), pickle.loads(pickle.dumps(marker))):
        assert isinstance(clone, FileScopedRefusal) and clone.kind == "ambiguous_markers" and clone == marker
    merged: list[str] = []
    merged.extend([marker])
    assert demote_file_scoped_refusals({"errors": merged, "warnings": []}) == 1


@pytest.mark.parametrize("client", ["grok", "cursor-cli"])
def test_a_fenced_agents_md_refusal_from_another_writer_still_does_not_roll_back(
    initialized_repo: Path, client: str
) -> None:
    """Each AGENTS.md writer's refusal keeps its kind through that client's own result merge into update-project."""
    init_project(initialized_repo, ide=client)
    agents = initialized_repo / "AGENTS.md"
    user_text = "# Agents\n\nTo delimit a block write:\n\n```markdown\n<!-- trw:start -->\n```\n"
    agents.write_text(user_text, encoding="utf-8")

    result = update_project(initialized_repo)

    assert not result["errors"], result["errors"]
    assert agents.read_text(encoding="utf-8") == user_text
    assert not any("rolled back" in w for w in result["warnings"])


def test_a_grok_update_names_the_kept_copy_of_the_users_agents_md(fake_git_repo: Path) -> None:
    """The grok install/update merges forwarded only created/updated/preserved/errors (learning L-Igk6's class), so the
    writer's warning naming where the user's previous AGENTS.md was kept never reached them."""
    init_project(fake_git_repo, ide="grok")
    agents = fake_git_repo / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    start, end = text.index("<!-- trw:start -->"), text.index("<!-- trw:end -->")
    agents.write_text(
        "# My agents\n\nmy own notes\n\n" + text[:start] + "<!-- trw:start -->\nstale TRW text\n" + text[end:],
        encoding="utf-8",
    )

    result = update_project(fake_git_repo)

    assert not result["errors"], result["errors"]
    assert "stale TRW text" not in agents.read_text(encoding="utf-8")
    assert any("AGENTS.md" in w and "previous version is kept at" in w for w in result["warnings"]), result["warnings"]
