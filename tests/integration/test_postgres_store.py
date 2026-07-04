"""Postgres store contract tests. Skipped unless ARO_TEST_DATABASE_URL points
at a reachable Postgres (CI provides one via a service container)."""

import os
import uuid

import pytest
from aro_runtime import discover_examples
from aro_runtime.store import create_store
from aro_schema import Attestation, AttestationDecision, digest_text
from aro_worker import process_once

DATABASE_URL = os.environ.get("ARO_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="ARO_TEST_DATABASE_URL not set; Postgres tests run in CI"
)


@pytest.fixture()
def store():
    pg = create_store(database_url=DATABASE_URL)
    # isolate each test run
    pg._conn.execute("TRUNCATE runs, queue, attestations")
    yield pg
    pg.close()


def test_factory_selects_postgres(store):
    assert type(store).__name__ == "PostgresRunStore"


def test_full_queue_lifecycle_on_postgres(store, examples_dir, tmp_path):
    examples = discover_examples(examples_dir)
    run_id = f"run-pg-{uuid.uuid4().hex[:8]}"
    example = examples["policy-violation-run"]
    store.save_placeholder(run_id, example.script.task, example.name)
    store.enqueue(run_id, example.name)

    assert process_once(store, examples, tmp_path) == run_id
    record = store.get_run(run_id)
    assert record["run"].status.value == "completed"
    assert record["run"].verdict.value == "blocked"
    (item,) = store.list_queue()
    assert item["status"] == "done"
    assert store.list_runs()[0]["id"] == run_id


def test_attestations_on_postgres(store):
    attestation = Attestation(
        id=f"att-{uuid.uuid4().hex[:8]}",
        run_id="run-x",
        decision=AttestationDecision.AMEND,
        declared_scope="scope",
        attested_by="Hao",
        subject_digest=digest_text("x"),
    )
    store.save_attestation(attestation)
    assert store.list_attestations("run-x") == [attestation]
