from aro_runtime import discover_examples
from aro_runtime.store import RunStore
from aro_worker import process_once


def test_worker_processes_queued_run(examples_dir, tmp_path):
    store = RunStore(tmp_path / "aro.db")
    examples = discover_examples(examples_dir)
    example = examples["policy-violation-run"]
    store.save_placeholder("run-queued-1", example.script.task, example.name)
    store.enqueue("run-queued-1", example.name)

    assert process_once(store, examples, tmp_path) == "run-queued-1"

    record = store.get_run("run-queued-1")
    assert record["run"].status.value == "completed"
    assert record["trace_path"] is not None
    denials = [d for d in record["run"].policy_decisions if d.decision.value == "deny"]
    assert len(denials) == 2

    assert process_once(store, examples, tmp_path) is None
