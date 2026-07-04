"""Queue worker: claims pending runs, executes them, retries with exponential
backoff, and dead-letters what keeps failing.

``process_once`` is the whole unit of work, factored out so tests can drive
the worker without a polling loop. ``fail_injector`` is the deterministic
chaos hook: it fails a claim on purpose so the retry and dead-letter paths
are exercised by tests and demos, not just asserted to exist
(env: ARO_CHAOS_FAIL_ATTEMPTS=N fails each item's first N attempts).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from aro_runtime import CompositeHooks, RunHooks, discover_examples
from aro_runtime.examples import Example, run_example
from aro_runtime.store import RunStore, create_store
from aro_schema import RunStatus, utcnow
from aro_telemetry.metrics import QUEUE_DEAD_LETTERS_TOTAL, QUEUE_RETRIES_TOTAL

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 2.0

FailInjector = Callable[[dict], None]


def _fail_placeholder(store: RunStore, claim: dict, error: str) -> None:
    record = store.get_run(claim["run_id"])
    if record is None:
        return
    run = record["run"]
    run.status = RunStatus.FAILED
    store.save_run(run, record["task"], example=record["example"], trace_path=record["trace_path"])


def process_once(
    store: RunStore,
    examples: dict[str, Example],
    data_dir: Path,
    hooks: RunHooks | None = None,
    fail_injector: FailInjector | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_s: float = DEFAULT_BACKOFF_S,
) -> str | None:
    """Claim and execute at most one queued run. Returns its run_id, or None.

    Failures (chaos, unknown example, tool crash) retry with exponential
    backoff up to ``max_attempts``, then land in the dead-letter state with
    the final error preserved on the queue row.
    """
    claim = store.claim_next()
    if claim is None:
        return None
    try:
        if fail_injector is not None:
            fail_injector(claim)
        example = examples.get(claim["example"])
        if example is None:
            raise LookupError(f"unknown example: {claim['example']}")
        trace_path = data_dir / "traces" / f"{claim['run_id']}.jsonl"
        run = run_example(example, run_id=claim["run_id"], hooks=hooks, trace_path=trace_path)
        store.save_run(run, example.script.task, example=example.name, trace_path=str(trace_path))
        store.mark(claim["id"], "done")
    except Exception as exc:  # noqa: BLE001 — queue boundary: every failure must land in retry/dead-letter
        attempts = int(claim.get("attempts") or 0) + 1
        if attempts >= max_attempts:
            store.mark_dead(claim["id"], attempts, str(exc))
            QUEUE_DEAD_LETTERS_TOTAL.inc()
            _fail_placeholder(store, claim, str(exc))
        else:
            delay = backoff_s * (2 ** (attempts - 1))
            store.retry(claim["id"], attempts, utcnow() + timedelta(seconds=delay), str(exc))
            QUEUE_RETRIES_TOTAL.inc()
    return claim["run_id"]


def chaos_injector_from_env() -> FailInjector | None:
    fail_attempts = int(os.environ.get("ARO_CHAOS_FAIL_ATTEMPTS", "0"))
    if fail_attempts <= 0:
        return None

    def inject(claim: dict) -> None:
        if int(claim.get("attempts") or 0) < fail_attempts:
            raise RuntimeError("chaos: injected failure (ARO_CHAOS_FAIL_ATTEMPTS)")

    return inject


def main() -> None:
    from aro_telemetry import MetricsHooks, TracingHooks, setup_tracing
    from prometheus_client import start_http_server

    examples_dir = Path(os.environ.get("ARO_EXAMPLES_DIR", REPO_ROOT / "examples"))
    data_dir = Path(os.environ.get("ARO_DATA_DIR", REPO_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    setup_tracing("aro-worker")
    metrics_port = int(os.environ.get("ARO_WORKER_METRICS_PORT", "9100"))
    start_http_server(metrics_port)

    store = create_store(sqlite_path=data_dir / "aro.db")
    examples = discover_examples(examples_dir)
    hooks = CompositeHooks([MetricsHooks(), TracingHooks()])
    fail_injector = chaos_injector_from_env()
    max_attempts = int(os.environ.get("ARO_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)))
    backoff_s = float(os.environ.get("ARO_RETRY_BACKOFF_S", str(DEFAULT_BACKOFF_S)))
    backend = type(store).__name__
    print(
        f"aro-worker: {len(examples)} examples, store={backend}, "
        f"metrics on :{metrics_port}, max_attempts={max_attempts}, polling queue"
    )
    try:
        while True:
            run_id = process_once(
                store,
                examples,
                data_dir,
                hooks=hooks,
                fail_injector=fail_injector,
                max_attempts=max_attempts,
                backoff_s=backoff_s,
            )
            if run_id:
                print(f"aro-worker: processed {run_id}")
            else:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("aro-worker: shutting down")


if __name__ == "__main__":
    main()
