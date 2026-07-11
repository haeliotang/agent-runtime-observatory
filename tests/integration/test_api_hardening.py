"""Rate limiting and attestation endpoints."""

import pytest
from aro_api.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(examples_dir, tmp_path):
    app = create_app(examples_dir=examples_dir, data_dir=tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def test_rate_limit_returns_429(examples_dir, tmp_path):
    app = create_app(examples_dir=examples_dir, data_dir=tmp_path, rate_limit_per_minute=3)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/api/runs", json={"example": "coding-agent-run"}).status_code == 200
        blocked = client.post("/api/runs", json={"example": "coding-agent-run"})
        assert blocked.status_code == 429
        # only run creation is limited; reads still work
        assert client.get("/api/runs").status_code == 200


def test_attestation_flow(client):
    run_id = client.post("/api/runs", json={"example": "policy-violation-run"}).json()["run_id"]

    # the run detail exposes the wutai-style verdict and an empty seat
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["verdict"] == "blocked"  # two denials in this example
    assert detail["attestations"] == []

    created = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "I reviewed the recorded denials and ratify the incident report.",
            "excluded_scope": "I do not ratify anything outside the recorded trace.",
            "proposed_by": "assistant",
            "attested_by": "Hao",
        },
    ).json()
    assert created["decision"] == "accept"
    assert created["subject_digest"].startswith("v1:sha256:")  # versioned canonical subject

    detail = client.get(f"/api/runs/{run_id}").json()
    assert len(detail["attestations"]) == 1
    assert detail["attestations"][0]["attested_by"] == "Hao"


def test_attestation_unknown_run_404(client):
    response = client.post(
        "/api/runs/run-nope/attestations",
        json={"decision": "accept", "declared_scope": "x", "attested_by": "Hao"},
    )
    assert response.status_code == 404


def test_queue_endpoint(client):
    client.post("/api/runs", json={"example": "coding-agent-run", "mode": "queued"})
    queue = client.get("/api/queue").json()
    assert len(queue) == 1 and queue[0]["status"] == "pending"
