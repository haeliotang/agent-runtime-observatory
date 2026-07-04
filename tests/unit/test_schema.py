from aro_schema import AgentRun, RunStatus, StepRecord, digest_obj, digest_text, utcnow


def test_digest_is_order_independent():
    assert digest_obj({"a": 1, "b": 2}) == digest_obj({"b": 2, "a": 1})


def test_digest_is_content_sensitive():
    assert digest_obj({"a": 1}) != digest_obj({"a": 2})
    assert digest_text("x") != digest_text("y")
    assert digest_text("x").startswith("sha256:")


def test_agent_run_roundtrip():
    run = AgentRun(
        id="run-1",
        task_id="task-1",
        agent="scripted@0.1",
        status=RunStatus.COMPLETED,
        started_at=utcnow(),
        finished_at=utcnow(),
        steps=[StepRecord(index=0, name="read_file", input_digest=digest_obj({}))],
    )
    restored = AgentRun.model_validate_json(run.model_dump_json())
    assert restored == run
