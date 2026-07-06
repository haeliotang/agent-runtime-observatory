from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def _coerce_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def read_jsonl(
    path: str | Path,
    schema: Callable[[dict[str, Any]], T] | type[T] | None = None,
    *,
    skip_bad_lines: bool = False,
) -> Iterator[dict[str, Any] | T]:
    with _coerce_path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                if skip_bad_lines:
                    continue
                raise ValueError(f"Invalid JSONL at line {line_number}: {path}") from None
            if not isinstance(row, dict):
                if skip_bad_lines:
                    continue
                raise ValueError(f"JSONL line {line_number} is not an object: {path}")
            if schema is None:
                yield row
            elif hasattr(schema, "from_dict"):
                yield getattr(schema, "from_dict")(row)
            else:
                yield schema(row)  # type: ignore[misc]


def write_jsonl(path: str | Path, items: Iterable[Any]) -> None:
    target = _coerce_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in items:
            if hasattr(item, "to_dict"):
                item = item.to_dict()
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def count_jsonl(path: str | Path) -> int:
    with _coerce_path(path).open("rb") as handle:
        return sum(1 for line in handle if line.strip())
