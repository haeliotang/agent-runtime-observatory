from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable

from wutai_clinic.schemas import Trajectory, Turn

from .constants import BFT_F, BOOTSTRAP_MIN_SAMPLES
from .hygiene import HygieneResult, run_target_hygiene

BFT_MAX_CONFIDENCE = 1.0 - math.pow(BFT_F, 2)
TRACK_REGEX = re.compile(r"\[T_(SEM|SENS|ACT)\]\s*(?:T=(\d+))?", re.IGNORECASE)


@dataclass
class PruneStats:
    input_count: int = 0
    output_count: int = 0
    pruned_turns: int = 0
    deduplicated: int = 0
    hygiene_filtered: int = 0


def stringify_content(value: object, max_chars: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        text = "\n".join(part for part in parts if part)
    elif isinstance(value, dict):
        text = (
            str(value.get("text", ""))
            if "text" in value
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
    else:
        text = str(value)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n[...truncated...]"
    return text


def _turn_dict(turn: Turn | dict) -> dict:
    return turn.to_dict() if isinstance(turn, Turn) else turn


def get_pruner_state_signature(turn: Turn | dict) -> str:
    data = _turn_dict(turn)
    role = data.get("role", "")
    content = stringify_content(data.get("content", ""))
    tool_call = data.get("tool_call", {})
    tool_name = tool_call.get("name", "") if tool_call else "none"
    tool_args = json.dumps(tool_call.get("arguments", {}), sort_keys=True) if tool_call else ""
    sig_str = f"{role}:{tool_name}:{tool_args}"
    if not tool_call and content:
        sig_str += f":{content[:100]}"
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()


def _turn_has_reasoning(turn: dict) -> bool:
    return bool(stringify_content(turn.get("reasoning", "")).strip())


def get_semantic_similarity(left: dict, right: dict) -> float:
    def extract_text_and_tracks(turn: dict) -> tuple[str, list[tuple[str, str]]]:
        tool = turn.get("tool_call") or {}
        tool_sig = f"{tool.get('name', '')}:{json.dumps(tool.get('arguments', {}), sort_keys=True)}"
        content = stringify_content(turn.get("content", ""))[:200]
        tracks = TRACK_REGEX.findall(content)
        reasoning = stringify_content(turn.get("reasoning", ""))[:300]
        return f"{tool_sig} {content} {reasoning}", tracks

    text_a, tracks_a = extract_text_and_tracks(left)
    text_b, tracks_b = extract_text_and_tracks(right)
    if tracks_a and tracks_b:
        t_a = {track[0].lower(): track[1] for track in tracks_a if track[1]}
        t_b = {track[0].lower(): track[1] for track in tracks_b if track[1]}
        for track_name in ["sem", "sens", "act"]:
            if track_name in t_a and track_name in t_b and t_a[track_name] != t_b[track_name]:
                return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


def calculate_window_str(window_states: list[str]) -> float:
    if len(window_states) < 2:
        return 0.0
    numerator = 0.0
    denominator = 0.0
    for i, left in enumerate(window_states):
        for j, right in enumerate(window_states):
            if i == j:
                continue
            weight = math.exp(-abs(i - j) / BFT_F)
            kernel = 1.0 if left == right else 0.0
            numerator += kernel * weight
            denominator += weight
    return numerator / denominator if denominator > 0 else 0.0


def calculate_window_str_semantic(window_turns: list[dict]) -> float:
    if len(window_turns) < 2:
        return 0.0
    numerator = 0.0
    denominator = 0.0
    for i, left in enumerate(window_turns):
        for j, right in enumerate(window_turns):
            if i == j:
                continue
            weight = math.exp(-abs(i - j) / BFT_F)
            kernel = get_semantic_similarity(left, right)
            numerator += kernel * weight
            denominator += weight
    return numerator / denominator if denominator > 0 else 0.0


def detect_cognitive_restart(turn: dict) -> bool:
    role = turn.get("role", "")
    content = stringify_content(turn.get("content", "")).lower()
    reasoning = stringify_content(turn.get("reasoning", "")).lower()
    tool_call = turn.get("tool_call", {})
    if tool_call:
        args = json.dumps(tool_call.get("arguments", {}))
        if "git stash" in args or "git checkout" in args or "git reset" in args:
            return True
    reset_keywords = [
        "start over",
        "let me restart",
        "try a completely different",
        "completely different approach",
        "revert back",
        "discard changes",
    ]
    if role == "assistant":
        return any(keyword in content for keyword in reset_keywords) or any(
            keyword in reasoning for keyword in reset_keywords
        )
    return False


def prune_single_trajectory(
    sft_turns: list[dict],
    *,
    semantic_kernel: bool = True,
) -> list[dict]:
    if len(sft_turns) <= BOOTSTRAP_MIN_SAMPLES:
        return sft_turns

    segments: list[list[dict]] = []
    current_segment: list[dict] = []
    for turn in sft_turns:
        if detect_cognitive_restart(turn) and len(current_segment) >= BOOTSTRAP_MIN_SAMPLES:
            if current_segment:
                current_segment[-1]["aha_signal"] = "pre_restart"
            turn["aha_signal"] = "cognitive_restart"
            segments.append(current_segment)
            current_segment = [turn]
        else:
            current_segment.append(turn)
    if current_segment:
        segments.append(current_segment)

    pruned_turns: list[dict] = []
    window_size = BOOTSTRAP_MIN_SAMPLES
    semantic_prune_threshold = (BFT_MAX_CONFIDENCE + (1.0 - BFT_F)) / 2.0
    for segment in segments:
        if len(segment) <= window_size:
            pruned_turns.extend(segment)
            continue
        use_semantic = semantic_kernel and any(_turn_has_reasoning(turn) for turn in segment)
        pruned_indices: set[int] = set()
        if use_semantic:
            index = 0
            while index < len(segment) - window_size + 1:
                window_turns = segment[index : index + window_size]
                if calculate_window_str_semantic(window_turns) >= semantic_prune_threshold:
                    for window_index in range(1, window_size):
                        global_index = index + window_index
                        if (
                            get_semantic_similarity(
                                segment[global_index], segment[global_index - 1]
                            )
                            > semantic_prune_threshold
                        ):
                            pruned_indices.add(global_index)
                    index += window_size
                else:
                    index += 1
        else:
            signatures = [get_pruner_state_signature(turn) for turn in segment]
            index = 0
            while index < len(segment) - window_size + 1:
                window_states = signatures[index : index + window_size]
                if calculate_window_str(window_states) >= BFT_MAX_CONFIDENCE:
                    seen: set[str] = set()
                    for window_index in range(window_size):
                        global_index = index + window_index
                        signature = signatures[global_index]
                        if signature in seen:
                            pruned_indices.add(global_index)
                        else:
                            seen.add(signature)
                    index += window_size
                else:
                    index += 1
        pruned_turns.extend(
            turn for index, turn in enumerate(segment) if index not in pruned_indices
        )
    return pruned_turns


def truncate_format_error_tail(sft_turns: list[dict]) -> list[dict]:
    if not sft_turns:
        return sft_turns
    tail_text = stringify_content(sft_turns[-1]).lower()
    if "format error" in tail_text and ("exit" in tail_text or "invalid format" in tail_text):
        return sft_turns[:-1]
    return sft_turns


def pruning_policy_for_source(source_kind: str) -> str:
    if source_kind == "swe_long":
        return "long_aggressive"
    if source_kind in {"gui_temporal", "physical_temporal"}:
        return "temporal_moderate"
    if source_kind == "mbpp_base":
        return "schema_only"
    return "short_expert_validation"


def apply_source_policy(sft_turns: list[dict], source_kind: str) -> tuple[list[dict], str]:
    policy = pruning_policy_for_source(source_kind)
    turns = truncate_format_error_tail(sft_turns)
    if policy == "long_aggressive":
        return prune_single_trajectory(turns, semantic_kernel=False), policy
    if policy == "temporal_moderate" and len(turns) > BOOTSTRAP_MIN_SAMPLES * 2:
        return prune_single_trajectory(turns, semantic_kernel=False), policy
    return turns, policy


def role_counts(sft_turns: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for turn in sft_turns:
        role = str(turn.get("role", ""))
        counts[role] = counts.get(role, 0) + 1
    return counts


def compute_quality_score(source_kind: str, sft_turns: list[dict]) -> float:
    counts = role_counts(sft_turns)
    assistant_count = counts.get("assistant", 0)
    user_count = counts.get("user", 0)
    tool_count = sum(1 for turn in sft_turns if turn.get("tool_call"))
    aha_count = sum(1 for turn in sft_turns if turn.get("aha_signal"))
    score = 1.0 + math.log1p(len(sft_turns)) * 0.5
    score += min(assistant_count, 20) * 0.08
    score += min(user_count, 20) * 0.04
    score += min(tool_count, 40) * 0.06
    score += aha_count * 0.1
    if source_kind == "swe_long":
        score += 1.5
    elif source_kind in {"gui_temporal", "physical_temporal"}:
        score += 1.0
    elif source_kind == "mbpp_base":
        score += 0.6
    else:
        score += 0.8
    final_assistant = next(
        (
            stringify_content(turn.get("content", ""))
            for turn in reversed(sft_turns)
            if turn.get("role") == "assistant"
        ),
        "",
    )
    if len(final_assistant.strip()) >= 20:
        score += 0.4
    return score


def prune_trajectory(trajectory: Trajectory) -> Trajectory:
    data = trajectory.to_dict()
    source_kind = str(data.get("_wutai_source_kind") or "general")
    cleaned_turns, policy = apply_source_policy(data["sft_turns"], source_kind)
    data["sft_turns"] = cleaned_turns
    data["_wutai_pruned_turn_count"] = max(0, len(trajectory.sft_turns) - len(cleaned_turns))
    data["_wutai_pruning_policy"] = policy
    data["_wutai_quality_score"] = compute_quality_score(source_kind, cleaned_turns)
    pruned = Trajectory.from_dict(data)
    return Trajectory(
        instance_id=pruned.instance_id,
        sft_turns=pruned.sft_turns,
        environment=pruned.environment,
        source=pruned.source,
        task=pruned.task,
        str_health_v1=pruned.str_health_v1,
        metadata=dict(pruned.metadata),
        id_field=pruned.id_field,
        turns_field=pruned.turns_field,
        has_source_field=pruned.has_source_field,
    )


def deduplicate(
    trajectories: list[Trajectory],
    key: Callable[[Trajectory], str] | str = "instance_id",
) -> list[Trajectory]:
    best: dict[str, Trajectory] = {}
    for trajectory in trajectories:
        group = getattr(trajectory, key) if isinstance(key, str) else key(trajectory)
        score = float(trajectory.metadata.get("_wutai_quality_score", 0.0) or 0.0)
        existing = best.get(group)
        existing_score = (
            float(existing.metadata.get("_wutai_quality_score", 0.0) or 0.0) if existing else -1.0
        )
        if existing is None or score >= existing_score:
            best[group] = trajectory
    return list(best.values())


def rank_by_health(trajectories: list[Trajectory]) -> list[Trajectory]:
    return sorted(
        trajectories,
        key=lambda trajectory: float(trajectory.metadata.get("_wutai_quality_score", 0.0) or 0.0),
        reverse=True,
    )


def prune_corpus(
    rows: list[dict],
    *,
    target_hygiene: bool = True,
    dedup: bool = True,
    rank: bool = False,
    input_file: str = "in-memory",
    output_file: str = "in-memory",
    quarantine_file: str = "in-memory",
) -> tuple[list[Trajectory], PruneStats, HygieneResult | None]:
    hygiene_result = None
    source_rows = rows
    stats = PruneStats(input_count=len(rows))
    if target_hygiene:
        hygiene_result = run_target_hygiene(
            rows,
            input_file=input_file,
            output_file=output_file,
            quarantine_file=quarantine_file,
        )
        source_rows = hygiene_result.rows
        stats.hygiene_filtered = len(rows) - len(source_rows)

    trajectories = [prune_trajectory(Trajectory.from_dict(row)) for row in source_rows]
    stats.pruned_turns = sum(
        int(trajectory.metadata.get("_wutai_pruned_turn_count", 0) or 0)
        for trajectory in trajectories
    )
    if dedup:
        before_dedup = len(trajectories)
        trajectories = deduplicate(trajectories)
        stats.deduplicated = before_dedup - len(trajectories)
    if rank:
        trajectories = rank_by_health(trajectories)
    stats.output_count = len(trajectories)
    return trajectories, stats, hygiene_result
