"""Retry, backoff, and dead-letter semantics — driven by the deterministic
chaos injector, not by luck."""

from datetime import timedelta

from aro_runtime import discover_examples
from aro_runtime.store import RunStore
from aro_schema import utcnow
from aro_worker import process_once


def _enqueue(store, examples, run_id="run-retry-1", example="coding-agent-run"):
    ex = examples[example]
    store.save_placeholder(run_id, ex.script.task, ex.name)
    store.enqueue(run_id, ex.name)


def _make_available_now(store):
    """Collapse the backoff window so tests don't sleep."""
    past = (utcnow() - timedelta(seconds=1)).isoformat()
    with store._lock:
        store._conn.execute("UPDATE queue SET available_at = ?", (past,))
        store._conn.commit()


def fail_first_attempts(n):
    def inject(claim):
        if int(claim.get("attempts") or 0) < n:
            raise RuntimeError("chaos: injected failure")

    return inject


def test_transient_failure_retries_then_succeeds(examples_dir, tmp_path):
    store = RunStore(tmp_path / "aro.db")
    examples = discover_examples(examples_dir)
    _enqueue(store, examples)

    injector = fail_first_attempts(1)  # fail attempt 0, succeed on retry
    assert process_once(store, examples, tmp_path, fail_injector=injector) == "run-retry-1"
    (item,) = store.list_queue()
    assert item["status"] == "pending" and item["attempts"] == 1
    assert "chaos" in item["last_error"]
    assert item["available_at"] is not None  # backoff scheduled

    assert process_once(store, examples, tmp_path, fail_injector=injector) is None  # backing off
    _make_available_now(store)
    assert process_once(store, examples, tmp_path, fail_injector=injector) == "run-retry-1"
    (item,) = store.list_queue()
    assert item["status"] == "done"
    assert store.get_run("run-retry-1")["run"].status.value == "completed"


def test_persistent_failure_dead_letters(examples_dir, tmp_path):
    store = RunStore(tmp_path / "aro.db")
    examples = discover_examples(examples_dir)
    _enqueue(store, examples, run_id="run-dead-1")

    injector = fail_first_attempts(99)  # never recovers
    for _ in range(3):
        _make_available_now(store)
        process_once(store, examples, tmp_path, fail_injector=injector, max_attempts=3)

    (item,) = store.list_queue()
    assert item["status"] == "dead" and item["attempts"] == 3
    assert store.get_run("run-dead-1")["run"].status.value == "failed"
    assert store.list_queue(status="dead") == [item]


def test_unknown_example_dead_letters(examples_dir, tmp_path):
    store = RunStore(tmp_path / "aro.db")
    examples = discover_examples(examples_dir)
    store.enqueue("run-ghost-1", "no-such-example")
    for _ in range(3):
        _make_available_now(store)
        process_once(store, examples, tmp_path, max_attempts=3)
    (item,) = store.list_queue()
    assert item["status"] == "dead"
    assert "unknown example" in item["last_error"]
