"""Policy engine: evaluates each step against a declarative rule bundle.

Semantics (documented in docs/object-model.md):
- first matching rule wins; no match falls through to the bundle default;
- ``deny`` blocks the step before execution;
- ``needs_review`` lets the step execute but records a decision and a risk
  signal, i.e. it creates review debt instead of silently proceeding.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import yaml
from aro_schema import Decision, Severity, canonical_json
from pydantic import BaseModel, Field


class PolicyRule(BaseModel):
    id: str
    description: str = ""
    tool: str | None = None
    args_regex: str | None = None
    domain_not_in: list[str] | None = None
    action: Decision = Decision.DENY
    severity: Severity = Severity.MEDIUM
    category: str = "policy"


class Policy(BaseModel):
    id: str
    description: str = ""
    default: Decision = Decision.ALLOW
    rules: list[PolicyRule] = Field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> Policy:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


class PolicyEngine:
    def __init__(self, policy: Policy):
        self.policy = policy

    @classmethod
    def from_file(cls, path: Path) -> PolicyEngine:
        return cls(Policy.from_file(path))

    def evaluate(self, tool: str, args: dict) -> tuple[Decision, PolicyRule | None, str]:
        """Return (decision, matched rule or None, human-readable reason)."""
        for rule in self.policy.rules:
            if rule.tool is not None and rule.tool != tool:
                continue
            if rule.args_regex is not None and not re.search(rule.args_regex, canonical_json(args)):
                continue
            if rule.domain_not_in is not None:
                url = args.get("url")
                if not isinstance(url, str):
                    continue
                host = urlparse(url).netloc
                if host in rule.domain_not_in:
                    continue
            reason = rule.description or f"rule {rule.id} matched tool {tool}"
            return rule.action, rule, reason
        return self.policy.default, None, "no rule matched; bundle default applied"
