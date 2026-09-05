"""A config key that does nothing must say so. PRD-QUAL-131-FR04.

``TRWConfig`` is ``extra="ignore"``: an undefined key is dropped in silence, and
``TRW_CONFIG_STRICT=1`` does not help because its fail-closed branch lives inside
an ``except`` that ``extra="ignore"`` never enters. So the operator who tuned a
knob learns nothing when the knob is removed. These tests pin the warning that
closes that gap, and the two properties that make it safe to ship: it never
prints the value, and it does not repeat.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_warned() -> object:
    """The dedup set is process-global; leaking it across tests hides repeats."""
    from trw_mcp.models.config import _retired_keys

    _retired_keys._reset_warned_keys()
    yield
    _retired_keys._reset_warned_keys()


def test_unknown_config_key_warns_with_key_name(capsys: pytest.CaptureFixture[str]) -> None:
    """The warning must name the offending key — a generic message is useless."""
    from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

    warned = warn_unrecognised_config_keys({"nudge_enabled": True, "not_a_field": 7}, {"nudge_enabled"})

    assert warned == ["not_a_field"]
    assert "not_a_field" in capsys.readouterr().err


def test_a_clean_config_produces_no_warning(capsys: pytest.CaptureFixture[str]) -> None:
    """Negative case. A false positive here is worse than a missing warning."""
    from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

    warned = warn_unrecognised_config_keys({"nudge_enabled": True}, {"nudge_enabled", "ceremony_mode"})

    assert warned == []
    assert capsys.readouterr().err == ""


def test_the_warning_never_prints_the_value(capsys: pytest.CaptureFixture[str]) -> None:
    """NFR03. A user may hold a credential-adjacent value under a retired key."""
    from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

    warn_unrecognised_config_keys({"legacy_token_key": "s3cr3t-do-not-print"}, set())

    err = capsys.readouterr().err
    assert "legacy_token_key" in err
    assert "s3cr3t-do-not-print" not in err


def test_a_key_warns_at_most_once_per_process(capsys: pytest.CaptureFixture[str]) -> None:
    """The machine-defaults file merges under the project file, and config reloads.

    Without the dedup, an operator carrying one stale key in ``~/.trw/config.yaml``
    would see the same line on every rebuild for the life of the session.
    """
    from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

    assert warn_unrecognised_config_keys({"gone_field": 1}, set()) == ["gone_field"]
    first = capsys.readouterr().err

    assert warn_unrecognised_config_keys({"gone_field": 1}, set()) == []
    assert capsys.readouterr().err == ""
    assert first.count("gone_field") == 1


def test_a_retired_key_names_its_replacement(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Retired, use X instead" and "retired, no replacement" must be distinguishable.

    Both are more useful than "unknown key": the first tells the operator where
    their setting went, the second tells them it is not coming back.
    """
    from trw_mcp.models.config import _retired_keys

    monkeypatch.setattr(
        _retired_keys,
        "retired_config_keys",
        lambda: {"old_weight": "client_profile.nudge_pool_weights", "orphan_knob": ""},
    )
    _retired_keys.warn_unrecognised_config_keys({"old_weight": 1, "orphan_knob": 2, "typo_knob": 3}, set())

    err = capsys.readouterr().err
    assert "old_weight" in err and "client_profile.nudge_pool_weights" in err
    assert "orphan_knob" in err and "no replacement" in err
    assert "typo_knob" in err and "typo" in err


def test_the_shipped_retired_map_parses_and_names_only_removed_keys() -> None:
    """Every entry must be a key TRWConfig no longer defines.

    An entry naming a live field would tell an operator their working knob is
    retired, which is a worse failure than the silence this replaces.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._retired_keys import retired_config_keys

    still_live = sorted(set(retired_config_keys()) & set(TRWConfig.model_fields))
    assert still_live == [], f"retired map names fields TRWConfig still defines: {still_live}"


def test_the_two_wd02_nudge_knobs_are_audible_on_removal(capsys: pytest.CaptureFixture[str]) -> None:
    """WD-02. ``nudge_urgency_mode``/``nudge_dedup_enabled`` are gone in 2.0.0.

    Both were live public knobs an operator could set, and both were no-ops:
    their only path out of ``TRWConfig`` was the ``surfaces`` projection, whose
    only reader had no production call site. Deleting a field an operator holds
    in ``.trw/config.yaml`` is silent by default (``extra="ignore"``), so the
    removal is only honest if the retired map carries them — otherwise the knob
    goes from doing nothing quietly to not existing quietly.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._retired_keys import retired_config_keys, warn_unrecognised_config_keys

    retired = retired_config_keys()
    for key in ("nudge_urgency_mode", "nudge_dedup_enabled"):
        assert key not in TRWConfig.model_fields, f"{key} is still a live field"
        assert key in retired, f"{key} was removed without a retired-map entry"

    warned = warn_unrecognised_config_keys(
        {"nudge_urgency_mode": "always_high", "nudge_dedup_enabled": False},
        set(TRWConfig.model_fields),
    )
    err = capsys.readouterr().err
    assert warned == ["nudge_dedup_enabled", "nudge_urgency_mode"]
    # The key is named; the value the operator set is never echoed.
    assert "nudge_urgency_mode" in err
    assert "always_high" not in err


def test_the_shipped_retired_map_is_valid_json() -> None:
    """Non-vacuity for the test above: a corrupt map would read as empty."""
    resource = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "config-retired-keys.json"
    payload = json.loads(resource.read_text(encoding="utf-8"))
    assert isinstance(payload["retired"], dict)


def test_the_loader_calls_the_warning_on_the_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring test. A warning nothing invokes is not a warning.

    Asserts the production ``_build_config`` path reaches the checker with the
    merged YAML keys — the seam where ``extra="ignore"`` would otherwise swallow
    them.
    """
    from trw_mcp.models.config import _loader

    seen: list[set[str]] = []

    def _spy(keys: object, defined: object) -> list[str]:
        seen.append(set(keys))  # type: ignore[arg-type]
        return []

    monkeypatch.setattr(_loader, "warn_unrecognised_config_keys", _spy)
    monkeypatch.setattr(_loader, "_read_yaml_overrides", lambda _path: {"retired_probe_key": 1})
    monkeypatch.setattr(_loader, "resolve_platform_api_key", lambda _path: "")

    _loader._build_config()

    assert seen, "the loader never consulted the unrecognised-key check"
    assert "retired_probe_key" in seen[0]


class TestExternallyOwnedKeysAreNotWarnedAbout:
    """A key another subsystem owns is legitimate, not a typo.

    `.trw/config.yaml` is not TRWConfig's private file. `cli/auth.py` writes
    `platform_org_name`/`platform_user_email` there on `trw-mcp auth login`
    (auth.py:351,353) and reads them back to render auth status
    (auth.py:300,302). Warning on those told every user of this repo that three
    working keys "have no effect" on every single invocation.

    A warning that cries wolf is worse than the silence it replaced: it trains
    the operator to ignore the next one, which may be about a genuinely dead
    knob. Found by a cross-cutting review that traced the write AND the read
    rather than stopping at "not a TRWConfig field".
    """

    def test_an_externally_owned_key_produces_no_warning(self) -> None:
        from trw_mcp.models.config._retired_keys import (
            _reset_warned_keys,
            warn_unrecognised_config_keys,
        )

        _reset_warned_keys()
        warned = warn_unrecognised_config_keys(["platform_org_name", "platform_user_email"], defined=["trw_dir"])

        assert warned == []

    def test_a_genuinely_unknown_key_is_still_warned_about(self) -> None:
        """Non-vacuity control.

        Without this, a filter that suppressed every key would satisfy the test
        above — which is the exact defect class this suppression is meant to
        avoid, applied to itself.
        """
        from trw_mcp.models.config._retired_keys import (
            _reset_warned_keys,
            warn_unrecognised_config_keys,
        )

        _reset_warned_keys()
        warned = warn_unrecognised_config_keys(["platform_org_name", "definitely_not_a_real_key"], defined=["trw_dir"])

        assert warned == ["definitely_not_a_real_key"]

    def test_the_owner_of_each_externally_owned_key_is_recorded(self) -> None:
        """The map must say WHO owns the key, not merely that someone does.

        An unattributed exemption is indistinguishable from a suppression, and
        the next reader cannot check whether it is still true.
        """
        from trw_mcp.models.config._retired_keys import externally_owned_config_keys

        owners = externally_owned_config_keys()

        assert owners, "the externally-owned map must not be empty"
        for key, owner in owners.items():
            assert owner.strip(), f"{key} claims an owner but names none"
