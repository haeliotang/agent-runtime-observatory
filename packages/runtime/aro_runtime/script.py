"""Scripted run definitions: the declarative input to the runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aro_schema import Goal, ReviewerSeat, Task
from pydantic import BaseModel, Field


class ScriptedStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Script(BaseModel):
    task: Task
    goal: Goal
    reviewer_seats: list[ReviewerSeat] = Field(default_factory=list)
    agent: str = "scripted@0.1"
    model: str | None = None
    steps: list[ScriptedStep]

    @classmethod
    def from_file(cls, path: Path) -> Script:
        return cls.model_validate(json.loads(Path(path).read_text()))
