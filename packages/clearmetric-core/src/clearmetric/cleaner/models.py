"""Cleaner models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["error", "warn"]


class Finding(BaseModel):
    check_id: str
    node_id: str | None = None
    severity: Severity | None = None
    message: str
    fix_hint: str | None = None
    tier: str | None = None


class CleanerReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
