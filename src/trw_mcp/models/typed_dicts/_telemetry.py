"""Telemetry TypedDicts — shapes for remote recall and platform communication."""

from __future__ import annotations

from typing_extensions import TypedDict


class RemoteSharedLearningDict(TypedDict, total=False):
    """A learning entry returned by the remote recall endpoint.

    Renamed from ``SharedLearning`` to avoid collision with the
    ``backend.models.database.SharedLearning`` SQLAlchemy ORM model.
    The ``*Dict`` suffix follows the project-wide TypedDict convention.
    """

    summary: str
    detail: str
    tags: list[str]
    impact: float
    source_project: str
    source_learning_id: str
