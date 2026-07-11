"""Scripted run definitions: the declarative input to the runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aro_schema import Goal, ReviewerSeat, Task
from pydantic import BaseModel, Field, model_validator


class ScriptedStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # Optional review-substrate annotations (ported from stillmirror-review's
    # allocation ledger): which rubric buckets this step serves, and whether
    # it supports the goal's mainline ("yes" | "no" | "unknown").
    allocated_to: list[str] = Field(default_factory=list)
    supports_goal: str = "unknown"


class Script(BaseModel):
    task: Task
    goal: Goal
    reviewer_seats: list[ReviewerSeat] = Field(default_factory=list)
    agent: str = "scripted@0.1"
    model: str | None = None
    steps: list[ScriptedStep]

    @model_validator(mode="after")
    def _owner_seat_is_declared(self) -> Script:
        # Structural accountability: a goal's owning seat must be a seat this
        # script actually declares, and seat ids must be unique — two people
        # sharing one seat id makes the accountable party ambiguous.
        ids = [seat.id for seat in self.reviewer_seats]
        if len(ids) != len(set(ids)):
            dupes = sorted({sid for sid in ids if ids.count(sid) > 1})
            raise ValueError(f"duplicate reviewer seat ids: {dupes}")
        if self.goal.owner_seat_id not in set(ids):
            raise ValueError(
                f"goal.owner_seat_id {self.goal.owner_seat_id!r} is not a declared "
                f"reviewer seat (declared: {sorted(ids)})"
            )
        return self

    @classmethod
    def from_file(cls, path: Path) -> Script:
        return cls.model_validate(json.loads(Path(path).read_text()))
