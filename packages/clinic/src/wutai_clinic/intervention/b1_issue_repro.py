"""Route B1 scientific core — issue-text-only reproduction (Amendment A).

This module is PURE and fully offline-testable. It is where B1's validity lives:
the deployable payload is built from the issue text ONLY, and the content-level
M2b gate (`b1_payload_leak_scan`) is the live-time check that proves the injected
reproduction did not touch the official test (FAIL_TO_PASS / test_patch) or any
oracle. If this gate is wrong, the whole experiment is invalid — so it errs
toward flagging leakage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

ISSUE_TEXT_ONLY = "issue_text_only"

# Tokens that, if present in an injected payload, indicate official-test-identity
# or answer leakage. Lowercased substring match.
OFFICIAL_TEST_IDENTITY_TOKENS = (
    "fail_to_pass",
    "pass_to_pass",
    "pass_to_fail",
    "test_patch",
    "test_oracle",
)
ANSWER_LEAK_TOKENS = (
    "gold_patch",
    "gold patch",
)

# Markers that a problem_statement contains an actionable, issue-only reproduction.
_REPRO_MARKERS = (
    "to reproduce",
    "steps to reproduce",
    "reproduction",
    "reproduce",
    "traceback (most recent call last)",
    "minimal reproducible",
    "how to reproduce",
)
_CODE_FENCE = re.compile(r"```")
_SHELL_OR_PY = re.compile(r"(?m)^\s*(?:\$|>>>|python[ \t]|pytest[ \t]|pip[ \t])")


@dataclass(frozen=True)
class IssueReproEligibility:
    instance_id: str
    eligible: bool
    markers: tuple[str, ...]
    reason: str


def issue_repro_eligibility(
    instance_id: str, problem_statement: str | None
) -> IssueReproEligibility:
    """Decide whether an instance's issue text carries an actionable reproduction
    derivable WITHOUT the official test. Amendment A §4 anchor screen."""
    text = (problem_statement or "").strip()
    if not text:
        return IssueReproEligibility(instance_id, False, (), "empty_problem_statement")
    lowered = text.lower()
    found: list[str] = [m for m in _REPRO_MARKERS if m in lowered]
    has_code = bool(_CODE_FENCE.search(text)) or bool(_SHELL_OR_PY.search(text))
    if has_code:
        found.append("code_or_command_block")
    eligible = bool(found) and has_code or "traceback (most recent call last)" in lowered
    reason = "actionable_issue_reproduction" if eligible else "no_actionable_issue_reproduction"
    return IssueReproEligibility(instance_id, eligible, tuple(found), reason)


def extract_issue_reproduction_steps(
    problem_statement: str | None, *, max_chars: int = 4000
) -> str:
    """Pull the reproduction-bearing part of the issue (code fences / repro section).
    Issue text only; never the official test."""
    text = (problem_statement or "").strip()
    if not text:
        return ""
    fences = re.findall(r"```.*?```", text, flags=re.DOTALL)
    if fences:
        joined = "\n".join(block.strip() for block in fences)
        return joined[:max_chars]
    # fall back to the paragraph following a repro marker, else the head of the issue
    lowered = text.lower()
    for marker in _REPRO_MARKERS:
        idx = lowered.find(marker)
        if idx != -1:
            return text[idx : idx + max_chars]
    return text[:max_chars]


def build_b1_payload(
    *,
    instance_id: str,
    problem_statement: str | None,
    repro_traceback: str | None,
) -> dict[str, Any]:
    """Assemble the B1 deployable payload from issue text + the traceback captured
    by running an ISSUE-DERIVED reproduction. `repro_traceback` is None at plan
    time (captured live); the payload is still provenance-stamped."""
    return {
        "instance_id": instance_id,
        "info_kind": "issue_text_reproduction",
        "payload_provenance": ISSUE_TEXT_ONLY,
        "issue_reproduction_steps": extract_issue_reproduction_steps(problem_statement),
        "issue_derived_repro_traceback": repro_traceback,
    }


def b1_payload_leak_scan(
    payload: dict[str, Any],
    *,
    fail_to_pass: list[str] | None = None,
    test_patch: str | None = None,
    gold_patch: str | None = None,
) -> list[str]:
    """M2b content-level gate. Returns a list of leak findings; empty == clean.

    Checks, in order of severity:
      1. provenance must be issue_text_only;
      2. no official-test-identity / answer tokens in payload strings;
      3. no FAIL_TO_PASS test node-id appears verbatim in the payload;
      4. no test_patch / gold_patch line appears verbatim in the payload.
    """
    findings: list[str] = []
    if payload.get("payload_provenance") != ISSUE_TEXT_ONLY:
        findings.append(f"provenance_not_issue_text_only:{payload.get('payload_provenance')}")

    blob = "\n".join(
        str(payload.get(k) or "")
        for k in ("issue_reproduction_steps", "issue_derived_repro_traceback")
    )
    low = blob.lower()
    for token in (*OFFICIAL_TEST_IDENTITY_TOKENS, *ANSWER_LEAK_TOKENS):
        if token in low:
            findings.append(f"token:{token}")

    for node_id in fail_to_pass or []:
        nid = str(node_id).strip()
        if nid and nid in blob:
            findings.append(f"fail_to_pass_node_in_payload:{nid}")

    def _significant_lines(text: str | None) -> list[str]:
        # For a diff (gold/test_patch), only ADDED ('+') lines are the fix / official
        # test being given away. Context (' ') and removed ('-') lines are existing
        # code the issue's repro legitimately shares — flagging them is a false positive
        # (it blocked every treatment arm on innocent context overlap). For non-diff
        # raw text, consider all lines.
        text = text or ""
        is_diff = "@@ " in text or text.lstrip().startswith("diff ")
        out = []
        for raw in text.splitlines():
            if is_diff:
                if not raw.startswith("+") or raw.startswith("+++"):
                    continue
                line = raw[1:].strip()
            else:
                line = raw.strip()
            if len(line) >= 24 and not line.startswith(("diff ", "@@", "index ", "+++", "---")):
                out.append(line)
        return out

    for line in _significant_lines(test_patch):
        if line in blob:
            findings.append("test_patch_line_in_payload")
            break
    for line in _significant_lines(gold_patch):
        if line in blob:
            findings.append("gold_patch_line_in_payload")
            break

    return findings


# --- issue-derived reproduction capture (Amendment B step ②) -------------------
# The executor runs a script INSIDE the SWE-bench container and returns combined
# stdout+stderr. It is injected so the construction + capture logic is fully
# offline-testable; only the executor itself touches the live container.
ReproCaptureExecutor = Callable[[str], str]

_FENCE_BLOCK = re.compile(r"```(?:[a-zA-Z0-9_+-]*)\n?(.*?)```", re.DOTALL)
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL)


@dataclass(frozen=True)
class ReproCaptureResult:
    script: str
    traceback: str | None
    ran: bool


def construct_repro_script(issue_reproduction_steps: str | None) -> str:
    """Build a best-effort runnable Python reproduction from the issue's code/REPL
    blocks ONLY. Strips REPL prompts and drops transcript output lines. Returns ""
    when the issue carries no usable code (caller then captures no traceback)."""
    text = issue_reproduction_steps or ""
    blocks = _FENCE_BLOCK.findall(text)
    if not blocks:
        return ""
    lines: list[str] = []
    for block in blocks:
        block_lines = block.splitlines()
        prompted = any(ln.lstrip().startswith((">>> ", "... ")) for ln in block_lines)
        for raw in block_lines:
            ln = raw.rstrip("\n")
            if prompted:
                stripped = ln.lstrip()
                if stripped.startswith(">>> ") or stripped.startswith("... "):
                    lines.append(stripped[4:])  # REPL input only; drop output lines
            else:
                lines.append(ln)
    return "\n".join(lines).strip()


def capture_issue_repro(
    *,
    issue_reproduction_steps: str | None,
    executor: ReproCaptureExecutor,
    max_chars: int = 4000,
) -> ReproCaptureResult:
    """Construct the issue-derived repro, run it via `executor` (container), and
    return the captured traceback. Never references the official test."""
    script = construct_repro_script(issue_reproduction_steps)
    if not script:
        return ReproCaptureResult(script="", traceback=None, ran=False)
    output = executor(script) or ""
    match = _TRACEBACK_RE.search(output)
    traceback = (match.group(0) if match else output).strip()[:max_chars]
    return ReproCaptureResult(script=script, traceback=traceback or None, ran=True)


def attach_captured_traceback(payload: dict[str, Any], traceback: str | None) -> dict[str, Any]:
    """Return a copy of the payload with the live-captured traceback filled in.
    Provenance is preserved (issue_text_only); re-run b1_payload_leak_scan after."""
    return {**payload, "issue_derived_repro_traceback": traceback}


__all__ = [
    "ISSUE_TEXT_ONLY",
    "OFFICIAL_TEST_IDENTITY_TOKENS",
    "IssueReproEligibility",
    "ReproCaptureExecutor",
    "ReproCaptureResult",
    "attach_captured_traceback",
    "b1_payload_leak_scan",
    "build_b1_payload",
    "capture_issue_repro",
    "construct_repro_script",
    "extract_issue_reproduction_steps",
    "issue_repro_eligibility",
]
