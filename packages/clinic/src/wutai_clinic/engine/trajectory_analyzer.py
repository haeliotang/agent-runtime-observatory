from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from wutai_clinic.io import read_jsonl
from wutai_clinic.schemas import Trajectory, Turn

from .constants import BFT_VARIANCE_PRIOR, BOOTSTRAP_MIN_SAMPLES, TOPOLOGICAL_IMPEDANCE
from .str_calculator import calculate_window_str, get_state_signature

EPISTEMIC_TOOLS = {"view_file", "grep_search", "list_dir", "read_url_content", "read_browser_page"}
PRAGMATIC_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}


@dataclass
class TrajectoryMetrics:
    steps: int
    epistemic_ratio: float
    tool_entropy: float
    convergence_gradient: float
    rollbacks: int
    error_recovery_latency: float
    hypothesis_shift_rate: float
    avg_str: float
    epistemic_token_efficiency: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _tool_name(turn: Turn) -> str:
    return turn.tool_call.name if turn.tool_call else ""


def _content_text(value: object) -> str:
    if isinstance(value, list):
        import json

        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _is_epistemic(turn: Turn) -> bool:
    name = _tool_name(turn)
    if name in EPISTEMIC_TOOLS:
        return True
    if name == "run_command":
        command = str((turn.tool_call.arguments if turn.tool_call else {}).get("command", ""))
        return any(token in command for token in ("cat", "grep", "ss", "ls", "find", "status"))
    return False


def analyze(trajectory: Trajectory) -> TrajectoryMetrics:
    assistant_turns = [turn for turn in trajectory.sft_turns if turn.role == "assistant"]
    steps = len(assistant_turns)
    if steps == 0:
        return TrajectoryMetrics(0, 0.0, 0.0, 0.0, 0, math.log(3.0), 0.0, 0.0, 0.0)

    epistemic = sum(1 for turn in assistant_turns if _is_epistemic(turn))
    tool_counts = Counter(_tool_name(turn) or "none" for turn in assistant_turns if turn.tool_call)
    tool_total = sum(tool_counts.values())
    entropy = 0.0
    if tool_total:
        entropy = -sum(
            (count / tool_total) * (math.log(count / tool_total) / math.log(3))
            for count in tool_counts.values()
        )

    midpoint = steps // 2
    first_half = assistant_turns[:midpoint]
    second_half = assistant_turns[midpoint:]
    first_ratio = (
        sum(1 for turn in first_half if _is_epistemic(turn)) / len(first_half)
        if first_half
        else 0.0
    )
    second_ratio = (
        sum(1 for turn in second_half if _is_epistemic(turn)) / len(second_half)
        if second_half
        else 0.0
    )

    recovery_steps: list[int] = []
    in_error = False
    error_start = 0
    rollbacks = 0
    for index, turn in enumerate(trajectory.sft_turns):
        if turn.role != "tool":
            continue
        content = _content_text(turn.content).lower()
        if any(token in content for token in ("error", "fail", "exception")):
            if not in_error:
                in_error = True
                error_start = index
            if any(token in content for token in ("rollback", "stash", "revert")):
                rollbacks += 1
        elif in_error:
            recovery_steps.append(max(1, (index - error_start) // 2))
            in_error = False

    file_switches = 0
    last_file = None
    for turn in assistant_turns:
        if not turn.tool_call:
            continue
        args = turn.tool_call.arguments
        target = (
            args.get("path")
            or args.get("TargetFile")
            or args.get("file")
            or args.get("command", "")
        )
        if not target:
            continue
        target_str = str(target)
        if last_file and target_str != last_file:
            file_switches += 1
        last_file = target_str

    use_semantic = any(turn.reasoning for turn in trajectory.sft_turns)
    str_scores = []
    for index in range(len(trajectory.sft_turns)):
        start = max(0, index - BOOTSTRAP_MIN_SAMPLES + 1)
        window = trajectory.sft_turns[start : index + 1]
        if len(window) < 2:
            continue
        if use_semantic:
            str_scores.append(calculate_window_str(window, mode="semantic"))
        else:
            signatures = [get_state_signature(turn) for turn in window]
            str_scores.append(calculate_window_str(signatures, mode="structural"))
    avg_str = statistics.mean(str_scores) if str_scores else 0.0

    return TrajectoryMetrics(
        steps=steps,
        epistemic_ratio=epistemic / steps,
        tool_entropy=entropy,
        convergence_gradient=first_ratio - second_ratio,
        rollbacks=rollbacks,
        error_recovery_latency=statistics.mean(recovery_steps) if recovery_steps else math.log(3.0),
        hypothesis_shift_rate=file_switches / steps if steps else 0.0,
        avg_str=avg_str,
        epistemic_token_efficiency=steps * (TOPOLOGICAL_IMPEDANCE * BFT_VARIANCE_PRIOR),
    )


def _iter_trajectories(source: str | Path | Iterable[Trajectory]) -> Iterable[Trajectory]:
    if isinstance(source, (str, Path)):
        yield from read_jsonl(source, Trajectory)  # type: ignore[misc]
        return
    yield from source


def analyze_corpus(trajectories: str | Path | Iterable[Trajectory]) -> dict[str, object]:
    rows = [analyze(trajectory) for trajectory in _iter_trajectories(trajectories)]
    if not rows:
        return {"total_trajectories": 0, "metrics": {}}
    data = [row.to_dict() for row in rows]

    def values(key: str) -> list[float]:
        return [float(row[key]) for row in data]

    steps = values("steps")
    epistemic_ratios = values("epistemic_ratio")
    tool_entropies = values("tool_entropy")
    convergences = values("convergence_gradient")
    rollbacks = values("rollbacks")
    latencies = values("error_recovery_latency")
    shifts = values("hypothesis_shift_rate")
    strs = values("avg_str")
    tokens = values("epistemic_token_efficiency")
    return {
        "total_trajectories": len(rows),
        "metrics": {
            "optimal_path_length": {
                "mean": statistics.mean(steps),
                "std": statistics.pstdev(steps) if len(steps) > 1 else 0.0,
                "min": int(min(steps)),
                "max": int(max(steps)),
            },
            "epistemic_pragmatic_ratio": {
                "mean": statistics.mean(epistemic_ratios),
                "std": statistics.pstdev(epistemic_ratios) if len(epistemic_ratios) > 1 else 0.0,
            },
            "tool_shannon_entropy": {
                "mean": statistics.mean(tool_entropies),
                "std": statistics.pstdev(tool_entropies) if len(tool_entropies) > 1 else 0.0,
            },
            "exploration_exploitation_gradient": {
                "mean": statistics.mean(convergences),
                "note": "Positive values prove high early exploration converging to efficient late exploitation.",
            },
            "total_rollbacks": int(sum(rollbacks)),
            "error_recovery_latency": {
                "mean": statistics.mean(latencies),
            },
            "hypothesis_shift_rate": {
                "mean": statistics.mean(shifts),
            },
            "soft_topological_return": {
                "mean": statistics.mean(strs),
            },
            "estimated_trits_consumption": {
                "mean": statistics.mean(tokens),
                "total": float(sum(tokens)),
            },
        },
    }
