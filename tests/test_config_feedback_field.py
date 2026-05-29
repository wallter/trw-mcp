"""Tests for the ``feedback`` config subsection (PRD-INFRA-132 FR07)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trw_mcp.models.config._fields_feedback import FeedbackFields
from trw_mcp.models.config._main import TRWConfig


def test_feedback_proactive_defaults_false() -> None:
    cfg = TRWConfig()
    assert cfg.feedback.proactive is False


def test_feedback_default_thresholds() -> None:
    cfg = TRWConfig()
    assert cfg.feedback.build_check_fail_threshold == 3
    assert cfg.feedback.unhandled_exception_threshold == 2
    assert cfg.feedback.bug_learning_threshold == 1


def test_feedback_fields_validates_types() -> None:
    # Negative thresholds are rejected (ge=1).
    with pytest.raises(ValidationError):
        FeedbackFields(build_check_fail_threshold=0)
    with pytest.raises(ValidationError):
        FeedbackFields(unhandled_exception_threshold=-1)
    with pytest.raises(ValidationError):
        FeedbackFields(bug_learning_threshold=0)


def test_feedback_fields_accepts_overrides() -> None:
    f = FeedbackFields(
        proactive=True,
        build_check_fail_threshold=5,
        unhandled_exception_threshold=10,
        bug_learning_threshold=2,
    )
    assert f.proactive is True
    assert f.build_check_fail_threshold == 5
    assert f.unhandled_exception_threshold == 10
    assert f.bug_learning_threshold == 2


def test_config_yaml_override_proactive(tmp_path: Path) -> None:
    """Overriding via init kwargs (the loader's path) propagates into TRWConfig.feedback."""
    overrides = yaml.safe_load(
        yaml.safe_dump(
            {
                "feedback": {
                    "proactive": True,
                    "build_check_fail_threshold": 7,
                }
            }
        )
    )
    cfg = TRWConfig(**overrides)
    assert cfg.feedback.proactive is True
    assert cfg.feedback.build_check_fail_threshold == 7
    # Untouched fields keep their defaults.
    assert cfg.feedback.unhandled_exception_threshold == 2
    assert cfg.feedback.bug_learning_threshold == 1
