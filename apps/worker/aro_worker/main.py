"""Queue worker: claims pending runs from the SQLite queue and executes them.

``process_once`` is the whole unit of work, factored out so tests can drive
the worker without a polling loop.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from aro_runtime import CompositeHooks, RunHooks, discover_examples
from aro_runtime.examples import Example, run_example
from aro_runtime.store import RunStore

REPO_ROOT = Path(__file__).resolve().parents[3]


def process_once(
    store: RunStore,
    examples: dict[str, Example],
    data_dir: Path,
    hooks: RunHooks | None = None,
) -> str | None:
    """Claim and execute at most one queued run. Returns its run_id, or None."""
    claim = store.claim_next()
    if claim is None:
        return None
    example = examples.get(claim["example"])
    if example is None:
        store.mark(claim["id"], "failed")
        return claim["run_id"]
    trace_path = data_dir / "traces" / f"{claim['run_id']}.jsonl"
    run = run_example(example, run_id=claim["run_id"], hooks=hooks, trace_path=trace_path)
    store.save_run(run, example.script.task, example=example.name, trace_path=str(trace_path))
    store.mark(claim["id"], "done")
    return claim["run_id"]


def main() -> None:
    from aro_telemetry import MetricsHooks, TracingHooks, setup_tracing

    examples_dir = Path(os.environ.get("ARO_EXAMPLES_DIR", REPO_ROOT / "examples"))
    data_dir = Path(os.environ.get("ARO_DATA_DIR", REPO_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    setup_tracing("aro-worker")
    metrics_port = int(os.environ.get("ARO_WORKER_METRICS_PORT", "9100"))
    from prometheus_client import start_http_server

    start_http_server(metrics_port)

    store = RunStore(data_dir / "aro.db")
    examples = discover_examples(examples_dir)
    hooks = CompositeHooks([MetricsHooks(), TracingHooks()])
    print(f"aro-worker: {len(examples)} examples, metrics on :{metrics_port}, polling queue")
    try:
        while True:
            run_id = process_once(store, examples, data_dir, hooks=hooks)
            if run_id:
                print(f"aro-worker: completed {run_id}")
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("aro-worker: shutting down")


if __name__ == "__main__":
    main()
