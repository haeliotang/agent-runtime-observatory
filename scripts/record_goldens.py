"""Regenerate golden traces for every example.

Run from the repo root: ``uv run python scripts/record_goldens.py``

Golden run ids are deterministic (``golden-<example>``) so that derived object
ids (policy decisions, evidence, artifacts) are stable across re-recordings;
only timestamps change, and timestamps are never compared.
"""

from pathlib import Path

from aro_runtime import discover_examples, run_example

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    for name, example in discover_examples(ROOT / "examples").items():
        trace_path = example.dir / "golden" / "trace.jsonl"
        run = run_example(example, run_id=f"golden-{name}", trace_path=trace_path)
        print(
            f"recorded {trace_path.relative_to(ROOT)} ({run.status.value}, {len(run.steps)} steps)"
        )


if __name__ == "__main__":
    main()
