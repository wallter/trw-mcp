"""Strict Pydantic models for the local SHA-256 code-index manifest."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CODE_INDEX_SCHEMA_VERSION: Literal["code-index-manifest/v1"] = "code-index-manifest/v1"


class CodeIndexStats(BaseModel):
    """Summary counters for a single code-index update."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    total_files: int = Field(ge=0)
    added: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    modified: int = Field(ge=0)
    deleted: int = Field(ge=0)
    skipped: int = Field(ge=0)


class CodeIndexFileRow(BaseModel):
    """One repo-relative file row in the manifest."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    indexed_at: datetime

    @field_validator("path")
    @classmethod
    def _path_must_be_repo_relative_posix(cls, value: str) -> str:
        if value.startswith(("/", "../")) or value == ".." or "/../" in value:
            raise ValueError("path must be a repo-relative POSIX path")
        if "\\" in value:
            raise ValueError("path must use POSIX separators")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256_must_be_lower_hex(cls, value: str) -> str:
        if not all(char in "0123456789abcdef" for char in value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class CodeIndexManifest(BaseModel):
    """Versioned local manifest persisted under ``.trw/code-index``."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["code-index-manifest/v1"]
    repo_root: str = Field(min_length=1)
    git_head: str | None
    generated_at: datetime
    files: list[CodeIndexFileRow]
    stats: CodeIndexStats


__all__ = [
    "CODE_INDEX_SCHEMA_VERSION",
    "CodeIndexFileRow",
    "CodeIndexManifest",
    "CodeIndexStats",
]
