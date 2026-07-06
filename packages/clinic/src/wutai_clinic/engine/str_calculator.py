from __future__ import annotations

import hashlib
import json
import math
import statistics
from difflib import SequenceMatcher
from typing import Any

from wutai_clinic.schemas import Trajectory, Turn

from .constants import BFT_F


def _turn_dict(turn: Turn | dict[str, Any]) -> dict[str, Any]:
    return turn.to_dict() if isinstance(turn, Turn) else turn


def get_state_signature(turn: Turn | dict[str, Any]) -> str:
    data = _turn_dict(turn)
    role = data.get("role", "")
    content = data.get("content", "")
    tool_call = data.get("tool_call") or {}
    tool_name = tool_call.get("name", "")
    tool_args = json.dumps(tool_call.get("arguments") or {}, sort_keys=True) if tool_call else ""
    sig_str = f"{role}:{tool_name}:{tool_args}"
    if not tool_call and content:
        sig_str += f":{str(content)[:100]}"
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()


def get_semantic_similarity(left: Turn | dict[str, Any], right: Turn | dict[str, Any]) -> float:
    def text(turn: Turn | dict[str, Any]) -> str:
        data = _turn_dict(turn)
        tool = data.get("tool_call") or {}
        tool_sig = f"{tool.get('name', '')}:{json.dumps(tool.get('arguments', {}), sort_keys=True)}"
        content_raw = data.get("content", "")
        if isinstance(content_raw, list):
            content = json.dumps(content_raw, ensure_ascii=False)[:200]
        else:
            content = str(content_raw)[:200]
        reasoning = str(data.get("reasoning", "") or "")[:300]
        return f"{tool_sig} {content} {reasoning}"

    return SequenceMatcher(None, text(left), text(right)).ratio()


def calculate_window_str(
    window: list[str] | list[Turn] | list[dict[str, Any]], mode: str = "structural"
) -> float:
    if len(window) < 2:
        return 0.0
    if mode not in {"structural", "semantic"}:
        raise ValueError("mode must be 'structural' or 'semantic'")
    numerator = 0.0
    denominator = 0.0
    for i, left in enumerate(window):
        for j, right in enumerate(window):
            if i == j:
                continue
            weight = math.exp(-abs(i - j) / BFT_F)
            if mode == "semantic":
                kernel = get_semantic_similarity(left, right)  # type: ignore[arg-type]
            else:
                left_sig = left if isinstance(left, str) else get_state_signature(left)
                right_sig = right if isinstance(right, str) else get_state_signature(right)
                kernel = 1.0 if left_sig == right_sig else 0.0
            numerator += kernel * weight
            denominator += weight
    return numerator / denominator if denominator > 0 else 0.0


def calculate_rolling_str(
    trajectory: Trajectory | list[Turn] | list[dict[str, Any]],
    window_size: int = 5,
    mode: str = "structural",
) -> list[float]:
    turns = trajectory.sft_turns if isinstance(trajectory, Trajectory) else trajectory
    values = []
    for index in range(len(turns)):
        window = turns[max(0, index - window_size + 1) : index + 1]
        values.append(calculate_window_str(window, mode=mode))
    return values


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def detect_str_anomaly(
    rolling: list[float],
    method: str = "iqr",
    threshold: float | None = None,
) -> list[int]:
    if len(rolling) < 3:
        return []
    if method == "iqr":
        q1 = _percentile(rolling, 0.25)
        q3 = _percentile(rolling, 0.75)
        iqr = q3 - q1
        if iqr == 0:
            return []
        fence = threshold if threshold is not None else math.sqrt(len(rolling)) / 2.0
        lower = q1 - fence * iqr
        upper = q3 + fence * iqr
        return [index for index, value in enumerate(rolling) if value < lower or value > upper]
    if method == "zscore":
        mean = statistics.mean(rolling)
        stdev = statistics.pstdev(rolling)
        if stdev == 0:
            return []
        cutoff = threshold if threshold is not None else math.sqrt(2.0 * math.log(len(rolling)))
        return [index for index, value in enumerate(rolling) if abs(value - mean) / stdev >= cutoff]
    raise ValueError("method must be 'iqr' or 'zscore'")
