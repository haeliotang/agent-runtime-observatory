import pytest
from aro_api.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(examples_dir, tmp_path):
    app = create_app(examples_dir=examples_dir, data_dir=tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def test_healthz(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["examples"] == 3


def test_list_examples(client):
    names = {e["name"] for e in client.get("/api/examples").json()}
    assert names == {"coding-agent-run", "document-research-run", "policy-violation-run"}


def test_sync_run_and_fetch(client):
    created = client.post("/api/runs", json={"example": "coding-agent-run"}).json()
    assert created["queued"] is False
    run_id = created["run_id"]
    assert created["run"]["status"] == "completed"
    assert len(created["run"]["steps"]) == 5

    listed = client.get("/api/runs").json()
    assert any(row["id"] == run_id for row in listed)

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["example"] == "coding-agent-run"
    assert detail["run"]["status"] == "completed"

    trace = client.get(f"/api/runs/{run_id}/trace")
    assert trace.status_code == 200
    assert trace.text.splitlines()[0].startswith('{"type": "run_start"')


def test_replay_recorded_run(client):
    run_id = client.post("/api/runs", json={"example": "policy-violation-run"}).json()["run_id"]
    report = client.post(f"/api/runs/{run_id}/replay").json()
    assert report["ok"] is True
    assert report["divergences"] == []
    assert report["steps_compared"] == 5


def test_unknown_example_404(client):
    assert client.post("/api/runs", json={"example": "nope"}).status_code == 404


def test_queued_run_creates_pending_placeholder(client):
    created = client.post(
        "/api/runs", json={"example": "document-research-run", "mode": "queued"}
    ).json()
    assert created["queued"] is True
    detail = client.get(f"/api/runs/{created['run_id']}").json()
    assert detail["run"]["status"] == "pending"


def test_metrics_exposed(client):
    client.post("/api/runs", json={"example": "coding-agent-run"})
    body = client.get("/metrics").text
    assert "aro_runs_total" in body
    assert "aro_steps_total" in body
