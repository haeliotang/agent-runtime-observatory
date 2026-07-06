from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

CANONICAL_TOOLS = {
    "run_command",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "view_file",
    "grep_search",
    "list_dir",
    "read_url_content",
    "read_browser_page",
    "code_synthesis",
    "continuous_action",
}

TOOL_REQUIRED_ARGS = {
    "run_command": ("command",),
    "view_file": ("path",),
    "list_dir": ("path",),
    "grep_search": ("pattern",),
    "write_to_file": ("path",),
    "replace_file_content": ("path",),
    "multi_replace_file_content": ("path",),
    "read_url_content": ("url",),
    "read_browser_page": ("url",),
    "continuous_action": ("payload",),
}

SHELL_TOOL_ALIASES = {
    "bash",
    "python",
    "python3",
    "pytest",
    "git",
    "grep",
    "rg",
    "find",
    "cat",
    "sed",
    "ls",
    "make",
    "npm",
    "pnpm",
    "bun",
    "submit",
}

DOMAIN_ACTION_ALIASES = {
    "vla_actuator",
    "robotic_joint_actuator",
    "actuate_environment",
    "bft_consensus_tactic",
}

RUN_COMMAND_ALIASES = {
    "run_python_or_sql",
    "ebpf_profiler_cmd",
}

THOUGHT_ACTION_RE = re.compile(r"(?im)^\s*(Thought|Action)\s*:\s*")
ACTION_LINE_RE = re.compile(r"(?ims)^\s*Action\s*:.*$")
TOOL_CALL_BLOCK_RE = re.compile(r"(?is)<tool_call>.*?</tool_call>")
TOOL_TAG_RE = re.compile(r"(?is)</?tool_call>")
WHITESPACE_RE = re.compile(r"\s+")
MOJIBAKE_RE = re.compile(r"(?:æ|ã|å|ð|ï|þ|Â|Ã|Î|Ï|ï¼|ã|ä¸|ç|æ|æ|é|å|çµ|ç|è¦||||)")
CHAIN_REFUSAL_RE = re.compile(
    r"(不能提供.*(?:深度|逐步|私密)?.*思(?:维|維).*链|"
    r"不能提供.*推导|"
    r"can't provide.*chain of thought|"
    r"cannot provide.*chain of thought)",
    re.IGNORECASE,
)
HYGIENE_VERSION = "phase2_6_target_hygiene_v1"


@dataclass
class HygieneResult:
    rows: list[dict[str, Any]]
    quarantine_rows: list[dict[str, Any]]
    manifest: dict[str, Any]


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(stringify(item) for item in value if stringify(item))
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "")
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def normalize_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"raw": arguments}
    return {"raw": stringify(arguments)}


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = stringify(value).strip()
        if text:
            return text
    return ""


def command_from_args(args: dict[str, Any], fallback_name: str = "") -> str:
    return first_nonempty(
        args.get("command"),
        args.get("cmd"),
        args.get("payload"),
        args.get("raw"),
        fallback_name,
    )


def tool_args_missing(name: str, args: dict[str, Any]) -> list[str]:
    missing = []
    for key in TOOL_REQUIRED_ARGS.get(name, ()):
        if not stringify(args.get(key, "")).strip():
            missing.append(key)
    return missing


def literal_call_kwargs(payload: str) -> tuple[str, dict[str, Any]] | None:
    try:
        node = ast.parse(payload.strip(), mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        func_name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        func_name = node.func.attr
    else:
        return None

    kwargs: dict[str, Any] = {}
    for kw in node.keywords:
        if kw.arg is None:
            continue
        try:
            kwargs[kw.arg] = ast.literal_eval(kw.value)
        except (ValueError, SyntaxError):
            kwargs[kw.arg] = stringify(getattr(kw.value, "id", ""))
    return func_name, kwargs


def state_payload_action(payload: str) -> str:
    code_match = re.search(r"['\"]code['\"]\s*:\s*['\"]([^'\"]+)['\"]", payload)
    if code_match:
        return code_match.group(1).strip()
    try:
        parsed = ast.literal_eval(payload)
    except (ValueError, SyntaxError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    value = parsed.get("value")
    if isinstance(value, dict):
        action_code = first_nonempty(value.get("code"), value.get("action"))
        if action_code:
            return action_code
    return first_nonempty(parsed.get("code"), parsed.get("action"))


def path_args(args: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    if args.get("path"):
        cleaned["path"] = stringify(args.get("path"))
    return cleaned


def map_str_replace_editor(args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    command = stringify(args.get("command", "")).strip().lower()
    path = stringify(args.get("path", ""))
    if command == "view":
        return "view_file", {"path": path} if path else {}
    if command in {"create", "write"}:
        return "write_to_file", {
            "path": path,
            "file_text": stringify(args.get("file_text", args.get("content", ""))),
        }
    if command in {"str_replace", "replace"}:
        return "replace_file_content", {
            "path": path,
            "old_str": stringify(args.get("old_str", "")),
            "new_str": stringify(args.get("new_str", "")),
        }
    if command == "insert":
        return "replace_file_content", {
            "path": path,
            "old_str": "",
            "new_str": stringify(args.get("new_str", args.get("insert", ""))),
        }
    return "replace_file_content", args


def normalize_canonical_tool(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if name == "run_command":
        command = first_nonempty(args.get("command"), args.get("cmd"), args.get("raw"))
        if command:
            return "run_command", {"command": command}
        action_code = state_payload_action(stringify(args.get("payload", "")))
        if action_code:
            return "continuous_action", {"payload": action_code}
        return "run_command", {"command": ""}
    if name == "grep_search":
        pattern = first_nonempty(args.get("pattern"), args.get("query"))
        cleaned = {"pattern": pattern}
        if args.get("path"):
            cleaned["path"] = stringify(args.get("path"))
        return "grep_search", cleaned
    if name == "view_file":
        return "view_file", path_args(args) or args
    if name == "list_dir":
        return "list_dir", path_args(args) or args
    if name == "write_to_file":
        return "write_to_file", {
            "path": stringify(args.get("path", "")),
            "file_text": stringify(args.get("file_text", args.get("content", ""))),
        }
    if name == "replace_file_content":
        return "replace_file_content", {
            "path": stringify(args.get("path", "")),
            "old_str": stringify(args.get("old_str", "")),
            "new_str": stringify(args.get("new_str", "")),
        }
    if name == "continuous_action":
        payload = first_nonempty(args.get("payload"), args.get("action"), args.get("raw"))
        return "continuous_action", {"payload": payload} if payload else args
    return name, args


def embedded_payload_tool(args: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    payload = first_nonempty(args.get("payload"), args.get("raw"))
    if not payload:
        return None

    fenced = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", payload, re.DOTALL)
    if fenced:
        command = fenced.group(1).strip()
        if command:
            return "run_command", {"command": command}

    parsed = literal_call_kwargs(payload)
    if not parsed:
        return None
    embedded_name, embedded_args = parsed
    name = embedded_name.lower()

    if name in SHELL_TOOL_ALIASES or name in RUN_COMMAND_ALIASES:
        return "run_command", {"command": command_from_args(embedded_args, name)}
    if name == "str_replace_editor":
        return map_str_replace_editor(embedded_args)
    if name in DOMAIN_ACTION_ALIASES:
        payload_text = command_from_args(embedded_args)
        return "continuous_action", {"payload": payload_text}
    if name in CANONICAL_TOOLS:
        return normalize_canonical_tool(name, embedded_args)
    return None


def missing_required_tool_args(tool_call: dict[str, Any]) -> list[str]:
    name = stringify(tool_call.get("name", ""))
    args = tool_call.get("arguments") or {}
    if not isinstance(args, dict):
        return list(TOOL_REQUIRED_ARGS.get(name, ()))
    return tool_args_missing(name, args)


def normalize_tool_call(tool_call: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(tool_call, dict):
        return None, "missing"

    raw_name = stringify(
        tool_call.get("name") or (tool_call.get("function") or {}).get("name") or ""
    ).strip()
    args = tool_call.get("arguments")
    if args is None and isinstance(tool_call.get("function"), dict):
        args = tool_call["function"].get("arguments", {})
    args = normalize_arguments(args)
    name = raw_name.lower()

    if not name:
        return None, "missing"
    if name == "exit":
        return None, "drop_exit"
    if name == "str_replace_editor":
        mapped_name, mapped_args = map_str_replace_editor(args)
        return {"name": mapped_name, "arguments": mapped_args}, f"map:{raw_name}->{mapped_name}"
    if name in SHELL_TOOL_ALIASES:
        return (
            {"name": "run_command", "arguments": {"command": command_from_args(args, name)}},
            f"map:{raw_name}->run_command",
        )
    if name in RUN_COMMAND_ALIASES:
        return (
            {"name": "run_command", "arguments": {"command": command_from_args(args)}},
            f"map:{raw_name}->run_command",
        )
    if name in DOMAIN_ACTION_ALIASES:
        payload = command_from_args(args)
        return (
            {"name": "continuous_action", "arguments": {"payload": payload}},
            f"map:{raw_name}->continuous_action",
        )
    if name in CANONICAL_TOOLS:
        mapped_name, mapped_args = normalize_canonical_tool(name, args)
        embedded = embedded_payload_tool(args)
        if tool_args_missing(mapped_name, mapped_args) and embedded:
            embedded_name, embedded_args = embedded
            if not tool_args_missing(embedded_name, embedded_args):
                return (
                    {"name": embedded_name, "arguments": embedded_args},
                    f"repair_payload:{raw_name}->{embedded_name}",
                )
        return {"name": mapped_name, "arguments": mapped_args}, "canonical"
    return None, f"drop_unknown:{raw_name}"


def strip_protocol_noise(text: str) -> tuple[str, Counter]:
    stats: Counter = Counter()
    original = text
    if TOOL_CALL_BLOCK_RE.search(text):
        stats["tool_call_blocks_removed"] += len(TOOL_CALL_BLOCK_RE.findall(text))
        text = TOOL_CALL_BLOCK_RE.sub("", text)
    if TOOL_TAG_RE.search(text):
        stats["dangling_tool_tags_removed"] += len(TOOL_TAG_RE.findall(text))
        text = TOOL_TAG_RE.sub("", text)
    if THOUGHT_ACTION_RE.search(text):
        stats["thought_action_labels_removed"] += len(THOUGHT_ACTION_RE.findall(text))
        text = THOUGHT_ACTION_RE.sub("", text)
    if ACTION_LINE_RE.search(text):
        stats["action_lines_removed"] += len(ACTION_LINE_RE.findall(text))
        text = ACTION_LINE_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    text = text.strip()
    if text != original:
        stats["assistant_content_repaired"] += 1
    return text, stats


def is_generic_completion(text: str) -> bool:
    normalized = WHITESPACE_RE.sub(" ", text.strip().lower())
    return normalized in {
        "task completed.",
        "task completed",
        "done.",
        "done",
        "completed.",
        "completed",
    }


def row_text_blob(row: dict[str, Any]) -> str:
    parts = []
    for turn in row.get("sft_turns", []):
        parts.append(stringify(turn.get("content", "")))
        parts.append(stringify(turn.get("reasoning", "")))
        if isinstance(turn.get("tool_call"), dict):
            parts.append(json.dumps(turn["tool_call"], ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


def quarantine_reason(row: dict[str, Any]) -> str | None:
    text = row_text_blob(row)
    if MOJIBAKE_RE.search(text):
        return "mojibake"
    if CHAIN_REFUSAL_RE.search(text):
        return "chain_of_thought_refusal"
    return None


def clean_turns(
    row: dict[str, Any], manifest_stats: Counter
) -> tuple[list[dict[str, Any]], Counter]:
    repairs: Counter = Counter()
    cleaned_turns: list[dict[str, Any]] = []

    for turn in row.get("sft_turns", []):
        if not isinstance(turn, dict):
            repairs["non_dict_turn_dropped"] += 1
            continue

        role = stringify(turn.get("role", "")).lower()
        if role not in {"system", "user", "assistant", "tool"}:
            repairs["invalid_role_dropped"] += 1
            continue

        if role != "assistant":
            content, content_repairs = strip_protocol_noise(stringify(turn.get("content", "")))
            repairs.update(content_repairs)
            if not content:
                repairs[f"{role}_empty_dropped"] += 1
                continue
            next_turn = {"role": role, "content": content}
            if "position_ids" in turn:
                next_turn["position_ids"] = turn["position_ids"]
            cleaned_turns.append(next_turn)
            continue

        manifest_stats["assistant_messages_seen"] += 1
        tool_call, tool_status = normalize_tool_call(turn.get("tool_call"))
        repairs[f"tool_status:{tool_status}"] += 1
        if tool_status.startswith("map:"):
            manifest_stats["tool_calls_mapped"] += 1
        if tool_status.startswith("drop_unknown:"):
            manifest_stats["unknown_tool_turns_dropped"] += 1
            continue
        if tool_status == "drop_exit":
            manifest_stats["exit_tool_turns_dropped"] += 1
            continue

        content, content_repairs = strip_protocol_noise(stringify(turn.get("content", "")))
        repairs.update(content_repairs)

        if tool_call:
            if tool_call["name"] not in CANONICAL_TOOLS:
                repairs[f"noncanonical_tool_after_mapping:{tool_call['name']}"] += 1
                manifest_stats["noncanonical_tool_after_mapping"] += 1
                continue
            missing_args = missing_required_tool_args(tool_call)
            if missing_args:
                repairs[
                    f"tool_call_missing_required_args:{tool_call['name']}:{','.join(missing_args)}"
                ] += 1
                manifest_stats["tool_call_missing_required_args"] += 1
                continue
            cleaned_turn = {"role": "assistant", "content": "", "tool_call": tool_call}
            for key in ["aha_signal", "position_ids"]:
                if key in turn:
                    cleaned_turn[key] = turn[key]
            if content and not is_generic_completion(content):
                cleaned_turn["_wutai_reasoning_summary"] = content[:500]
                manifest_stats["assistant_tool_text_moved_to_metadata"] += 1
            cleaned_turns.append(cleaned_turn)
            manifest_stats["assistant_tool_calls_kept"] += 1
            continue

        if not content or is_generic_completion(content):
            repairs["assistant_text_turn_dropped"] += 1
            manifest_stats["assistant_text_turns_dropped"] += 1
            continue
        cleaned_turn = {"role": "assistant", "content": content}
        for key in ["aha_signal", "position_ids"]:
            if key in turn:
                cleaned_turn[key] = turn[key]
        cleaned_turns.append(cleaned_turn)
        manifest_stats["assistant_text_turns_kept"] += 1

    return cleaned_turns, repairs


def validation_failure(turns: list[dict[str, Any]]) -> str | None:
    if not turns:
        return "empty_after_hygiene"
    roles = Counter(turn.get("role") for turn in turns)
    if roles.get("assistant", 0) == 0:
        return "no_assistant_after_hygiene"
    if roles.get("user", 0) == 0 and roles.get("tool", 0) == 0:
        return "no_user_or_tool_after_hygiene"
    return None


def scan_residuals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counters: Counter = Counter()
    invalid_names: Counter = Counter()
    for row in rows:
        row_seen = set()
        for turn in row.get("sft_turns", []):
            content = stringify(turn.get("content", ""))
            if turn.get("role") == "assistant":
                if re.search(r"(?im)^\s*Thought\s*:", content):
                    counters["assistant_thought_residual_messages"] += 1
                    row_seen.add("assistant_thought_residual_rows")
                if re.search(r"(?im)^\s*Action\s*:", content):
                    counters["assistant_action_residual_messages"] += 1
                    row_seen.add("assistant_action_residual_rows")
                if "<tool_call>" in content.lower():
                    counters["assistant_tool_call_tag_residual_messages"] += 1
                    row_seen.add("assistant_tool_call_tag_residual_rows")
                tool_call = turn.get("tool_call")
                if isinstance(tool_call, dict):
                    name = stringify(tool_call.get("name", ""))
                    if name == "exit":
                        counters["assistant_exit_tool_calls"] += 1
                    if name not in CANONICAL_TOOLS:
                        counters["invalid_tool_names"] += 1
                        invalid_names[name] += 1
                    missing_args = missing_required_tool_args(tool_call)
                    if missing_args:
                        counters["assistant_tool_call_missing_required_args"] += 1
                        row_seen.add("assistant_tool_call_missing_required_arg_rows")
            if MOJIBAKE_RE.search(content):
                counters["mojibake_messages"] += 1
                row_seen.add("mojibake_rows")
        for key in row_seen:
            counters[key] += 1
    return {
        "counters": dict(sorted(counters.items())),
        "invalid_tool_names": dict(sorted(invalid_names.items())),
    }


def run_target_hygiene(
    rows: Iterable[dict[str, Any]],
    *,
    input_file: str = "in-memory",
    output_file: str = "in-memory",
    quarantine_file: str = "in-memory",
) -> HygieneResult:
    output_rows: list[dict[str, Any]] = []
    quarantine_rows: list[dict[str, Any]] = []
    global_stats: Counter = Counter()
    by_source: dict[str, Counter] = defaultdict(Counter)
    tool_status_counts: Counter = Counter()

    for row_index, row in enumerate(rows):
        global_stats["input_rows"] += 1
        source = stringify(row.get("_wutai_source_file", "unknown_source.jsonl"))
        by_source[source]["input_rows"] += 1

        reason = quarantine_reason(row)
        if reason:
            quarantine_row = dict(row)
            quarantine_row["_wutai_hygiene_quarantine_reason"] = reason
            quarantine_row["_wutai_hygiene_source_index"] = row_index
            quarantine_rows.append(quarantine_row)
            global_stats[f"quarantine:{reason}"] += 1
            by_source[source][f"quarantine:{reason}"] += 1
            continue

        before_turns = row.get("sft_turns", [])
        cleaned, repairs = clean_turns(row, global_stats)
        tool_status_counts.update(
            {
                key.removeprefix("tool_status:"): value
                for key, value in repairs.items()
                if key.startswith("tool_status:")
            }
        )

        failure = validation_failure(cleaned)
        if failure:
            quarantine_row = dict(row)
            quarantine_row["_wutai_hygiene_quarantine_reason"] = failure
            quarantine_row["_wutai_hygiene_source_index"] = row_index
            quarantine_row["_wutai_hygiene_repairs"] = dict(sorted(repairs.items()))
            quarantine_rows.append(quarantine_row)
            global_stats[f"quarantine:{failure}"] += 1
            by_source[source][f"quarantine:{failure}"] += 1
            continue

        next_row = dict(row)
        next_row["sft_turns"] = cleaned
        next_row["_wutai_hygiene"] = {
            "version": HYGIENE_VERSION,
            "source_index": row_index,
            "raw_turn_count": len(before_turns) if isinstance(before_turns, list) else 0,
            "clean_turn_count": len(cleaned),
            "repairs": dict(sorted(repairs.items())),
        }
        next_row["_wutai_hygienic_index"] = len(output_rows)
        output_rows.append(next_row)
        global_stats["output_rows"] += 1
        global_stats["raw_turns"] += len(before_turns) if isinstance(before_turns, list) else 0
        global_stats["clean_turns"] += len(cleaned)
        by_source[source]["output_rows"] += 1
        by_source[source]["raw_turns"] += len(before_turns) if isinstance(before_turns, list) else 0
        by_source[source]["clean_turns"] += len(cleaned)
        for key, value in repairs.items():
            by_source[source][key] += value

    residuals = scan_residuals(output_rows)
    manifest = {
        "phase": "2.6",
        "version": HYGIENE_VERSION,
        "input_file": input_file,
        "output_file": output_file,
        "quarantine_file": quarantine_file,
        "total_raw": global_stats.get("input_rows", 0),
        "total_normalized": global_stats.get("input_rows", 0),
        "total_purified": global_stats.get("output_rows", 0),
        "total_filtered": sum(
            value for key, value in global_stats.items() if key.startswith("quarantine:")
        ),
        "total_pruned_turns": max(
            0, global_stats.get("raw_turns", 0) - global_stats.get("clean_turns", 0)
        ),
        "summary": dict(sorted(global_stats.items())),
        "tool_status_counts": dict(sorted(tool_status_counts.items())),
        "canonical_tools": sorted(CANONICAL_TOOLS),
        "tool_required_args": {
            name: list(args) for name, args in sorted(TOOL_REQUIRED_ARGS.items())
        },
        "residuals": residuals,
        "by_source": {
            source: dict(sorted(counts.items())) for source, counts in sorted(by_source.items())
        },
        "promotion_gate": {
            "invalid_tool_names": residuals["counters"].get("invalid_tool_names", 0),
            "assistant_exit_tool_calls": residuals["counters"].get("assistant_exit_tool_calls", 0),
            "assistant_thought_residual_messages": residuals["counters"].get(
                "assistant_thought_residual_messages", 0
            ),
            "assistant_action_residual_messages": residuals["counters"].get(
                "assistant_action_residual_messages", 0
            ),
            "assistant_tool_call_tag_residual_messages": residuals["counters"].get(
                "assistant_tool_call_tag_residual_messages", 0
            ),
            "mojibake_messages": residuals["counters"].get("mojibake_messages", 0),
            "assistant_tool_call_missing_required_args": residuals["counters"].get(
                "assistant_tool_call_missing_required_args", 0
            ),
        },
    }
    return HygieneResult(rows=output_rows, quarantine_rows=quarantine_rows, manifest=manifest)
