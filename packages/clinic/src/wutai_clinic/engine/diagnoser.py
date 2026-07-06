from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from wutai_clinic.schemas import Trajectory, TrajectoryDiagnosis, TurningPointCandidate

from .str_calculator import calculate_rolling_str

DIAGNOSTIC_VERSION = "phase311_diagnosis_lite_v1"


ERROR_MARKERS = (
    "error",
    "exception",
    "failed",
    "failure",
    "invalid",
    "not found",
    "timeout",
    "traceback",
)


def action_family(action: str) -> str:
    lowered = action.lower().strip()
    if not lowered:
        return "none"
    if "str_replace_editor" in lowered or "replace_file_content" in lowered:
        return "file_edit"
    if "write_to_file" in lowered or "create_file" in lowered:
        return "file_write"
    if any(token in lowered for token in ("view_file", " cat ", " sed ", "head ", "tail ")):
        return "file_read"
    if any(token in lowered for token in ("grep", "rg ", "find ", "ls ")):
        return "search"
    if any(token in lowered for token in ("pytest", "tox", "npm test", "pnpm test", " test ")):
        return "test"
    if any(token in lowered for token in ("git diff", "git status", "git log")):
        return "git_inspect"
    if any(token in lowered for token in ("python", "bash", "sh ", "run_command", "execute")):
        return "command"
    if any(token in lowered for token in ("submit", "exit")):
        return "terminal"
    return "other"


def observation_class(observation: str) -> str:
    lowered = observation.lower().strip()
    if not lowered:
        return "empty"
    if any(marker in lowered for marker in ERROR_MARKERS):
        return "error"
    if any(marker in lowered for marker in ("success", "passed", "done", "completed")):
        return "success"
    return "neutral"


def canonical_step(step: dict[str, Any]) -> dict[str, str]:
    action = str(step.get("action") or "")
    observation = str(step.get("observation") or "")
    state = step.get("state") if isinstance(step.get("state"), dict) else {}
    return {
        "action_family": action_family(action),
        "observation_class": observation_class(observation),
        "working_dir_class": "testbed"
        if "/testbed" in str(state.get("working_dir", ""))
        else "other",
    }


def group_features(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["trajectory_id"]].append(row)
    for feature_rows in grouped.values():
        feature_rows.sort(key=lambda row: row["prefix_index"])
    return dict(grouped)


def validation_gap_lengths(states: list[dict[str, str]]) -> list[int]:
    gaps: list[int] = []
    last_edit = 0
    last_validation = 0
    for index, state in enumerate(states, start=1):
        family = state["action_family"]
        if family in {"file_edit", "file_write"}:
            last_edit = index
        if family in {"test", "git_inspect"}:
            last_validation = index
        gaps.append(index - last_edit if last_edit and last_edit > last_validation else 0)
    return gaps


def recent_same_action_lengths(states: list[dict[str, str]]) -> list[int]:
    lengths: list[int] = []
    current = None
    streak = 0
    for state in states:
        family = state["action_family"]
        if family == current:
            streak += 1
        else:
            current = family
            streak = 1
        lengths.append(streak)
    return lengths


def reason_codes(
    features: dict[str, Any],
    state: dict[str, str],
    validation_gap: int,
    same_action_streak: int,
) -> list[str]:
    reasons = []
    if features["online_str_v1"] >= 0.50 or features["recurrence_persistence"] >= 0.35:
        reasons.append("recurrence_spike")
    if features["duplicate_ratio"] >= 0.45 or features["adjacent_repeat_ratio"] >= 0.35:
        reasons.append("loop_or_duplicate_pattern")
    if features["error_streak"] >= 2 or state["observation_class"] == "error":
        reasons.append("error_streak_or_error_observation")
    if validation_gap >= 3:
        reasons.append("validation_gap_after_edit")
    if same_action_streak >= 4 and state["action_family"] not in {"none", "terminal"}:
        reasons.append("same_action_family_streak")
    if features["step_count"] >= 40 and features["action_entropy"] <= 1.0:
        reasons.append("long_run_low_action_entropy")
    return reasons


def diagnostic_score(
    features: dict[str, Any],
    validation_gap: int,
    same_action_streak: int,
) -> float:
    recurrence = max(
        features["online_str_v1"],
        features["recurrence_persistence"],
        features["duplicate_ratio"],
        features["adjacent_repeat_ratio"],
    )
    error = min(1.0, features["error_streak"] / 3)
    validation = min(1.0, validation_gap / 6)
    same_action = min(1.0, max(0, same_action_streak - 1) / 5)
    entropy = 1.0 - min(1.0, max(0.0, features["action_entropy"]) / 3)
    score = (
        0.30 * recurrence + 0.25 * error + 0.25 * validation + 0.10 * same_action + 0.10 * entropy
    )
    return round(min(1.0, score), 6)


def diagnose_from_features(
    *,
    chain_row: dict[str, Any],
    feature_rows: list[dict[str, Any]],
    canonical_states: list[dict[str, str]],
) -> dict[str, Any]:
    gaps = validation_gap_lengths(canonical_states)
    same_action = recent_same_action_lengths(canonical_states)
    candidates = []
    reason_counter: Counter[str] = Counter()
    for row in feature_rows:
        prefix_index = row["prefix_index"]
        state = canonical_states[prefix_index - 1] if prefix_index <= len(canonical_states) else {}
        features = row["features"]
        gap = gaps[prefix_index - 1] if prefix_index <= len(gaps) else 0
        streak = same_action[prefix_index - 1] if prefix_index <= len(same_action) else 0
        reasons = reason_codes(features, state, gap, streak)
        score = diagnostic_score(features, gap, streak)
        if reasons and score >= 0.35:
            reason_counter.update(reasons)
            candidates.append(
                {
                    "prefix_index": prefix_index,
                    "prefix_sha256": row["prefix_sha256"],
                    "diagnostic_score": score,
                    "reason_codes": reasons,
                    "state_class": {
                        "action_family": state.get("action_family", "unknown"),
                        "observation_class": state.get("observation_class", "unknown"),
                    },
                    "prefix_only_context": {
                        "validation_gap_steps": gap,
                        "same_action_family_streak": streak,
                        "step_count": features["step_count"],
                    },
                }
            )
    candidates.sort(key=lambda item: (-item["diagnostic_score"], item["prefix_index"]))
    first_actionable = min((item["prefix_index"] for item in candidates), default=None)
    return {
        "phase": "3.11",
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "trajectory_id": chain_row["trajectory_id"],
        "source_task_id": chain_row["source_task_id"],
        "source_family": chain_row["source_family"],
        "run_window": chain_row["run_window"],
        "candidate_selection_basis": "prefix_only_structural_features_and_canonical_state_classes",
        "candidate_count": len(candidates),
        "first_actionable_prefix_index": first_actionable,
        "top_transition_candidates": candidates[:3],
        "reason_summary": dict(sorted(reason_counter.items())),
        "diagnostic_decision": (
            "audit_candidate_present" if candidates else "no_deterministic_audit_candidate"
        ),
        "claim_boundary": "audit_only_not_predictive_evidence",
    }


def _feature_chain_row(
    trajectory: Trajectory | dict[str, Any],
    feature_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    first = feature_rows[0] if feature_rows else {}
    if isinstance(trajectory, Trajectory):
        trajectory_id = trajectory.instance_id
    else:
        trajectory_id = str(
            trajectory.get("trajectory_id")
            or trajectory.get("instance_id")
            or first.get("trajectory_id")
            or ""
        )
    return {
        "trajectory_id": trajectory_id,
        "source_task_id": first.get("source_task_id", ""),
        "source_family": first.get("source_family", ""),
        "run_window": first.get("run_window", ""),
    }


def _canonical_states_from_trajectory(
    trajectory: Trajectory | dict[str, Any],
) -> list[dict[str, str]]:
    if isinstance(trajectory, Trajectory):
        raw_steps = [turn.to_dict() for turn in trajectory.sft_turns]
    else:
        raw_steps = trajectory.get("trajectory") or trajectory.get("sft_turns") or []
    return [canonical_step(step) for step in raw_steps if isinstance(step, dict)]


def diagnose(
    trajectory: Trajectory | dict[str, Any],
    features: list[dict[str, Any]] | None = None,
    threshold: float = 0.5,
    *,
    chain_row: dict[str, Any] | None = None,
    canonical_states: list[dict[str, str]] | None = None,
) -> TrajectoryDiagnosis | dict[str, Any]:
    if features is not None:
        feature_rows = sorted(features, key=lambda row: row["prefix_index"])
        return diagnose_from_features(
            chain_row=chain_row or _feature_chain_row(trajectory, feature_rows),
            feature_rows=feature_rows,
            canonical_states=canonical_states or _canonical_states_from_trajectory(trajectory),
        )
    if not isinstance(trajectory, Trajectory):
        trajectory = Trajectory.from_dict(trajectory)
    rolling = calculate_rolling_str(trajectory)
    candidates: list[TurningPointCandidate] = []
    reason_summary: Counter[str] = Counter()
    for index, value in enumerate(rolling, start=1):
        reasons: list[str] = []
        if value >= threshold:
            reasons.append("recurrence_detected")
        turn = trajectory.sft_turns[index - 1]
        if turn.role == "tool" and any(
            token in str(turn.content).lower() for token in ("error", "fail")
        ):
            reasons.append("error_streak")
        if reasons:
            reason_summary.update(reasons)
            candidates.append(
                TurningPointCandidate(
                    prefix_index=index,
                    state_class=reasons[0],
                    feature_snapshot={"online_str_v1": value},
                    confidence=value,
                    reason_codes=reasons,
                )
            )
    return TrajectoryDiagnosis(
        instance_id=trajectory.instance_id,
        candidates=candidates,
        turn_count=len(trajectory.sft_turns),
        metadata={"reason_summary": dict(reason_summary), "claim_boundary": "audit_only"},
    )
