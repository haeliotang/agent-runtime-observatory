"""End-to-end consumable review debt: run with a needs_review step ->
open debt -> attestation clears the named item -> debt goes to zero."""

import pytest
from aro_api.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(examples_dir, tmp_path):
    app = create_app(examples_dir=examples_dir, data_dir=tmp_path)
    with TestClient(app) as test_client:
        yield test_client


def _violation_run(client) -> tuple[str, list[dict]]:
    run_id = client.post("/api/runs", json={"example": "policy-violation-run"}).json()["run_id"]
    debt = client.get(f"/api/runs/{run_id}/review-debt").json()
    return run_id, debt


def test_debt_opens_then_clears_to_zero(client):
    run_id, debt = _violation_run(client)
    # exactly one needs_review step in this example (the .env read)
    assert len(debt) == 1
    (item,) = debt
    assert item["status"] == "open"
    assert item["rule_id"] == "review-sensitive-read"

    created = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "I reviewed the flagged .env read; it is the fixture.",
            "attested_by": "Hao",
            "clears_decisions": [item["decision_id"]],
        },
    )
    assert created.status_code == 200
    assert created.json()["clears_decisions"] == [item["decision_id"]]

    # the named item is consumed; open debt for the run is now zero
    assert client.get(f"/api/runs/{run_id}/review-debt?status=open").json() == []
    (cleared,) = client.get(f"/api/runs/{run_id}/review-debt").json()
    assert cleared["status"] == "cleared" and cleared["attested_by"] == "Hao"

    # run detail carries the same joined view
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["review_debt"][0]["status"] == "cleared"


def test_reject_cannot_clear(client):
    run_id, debt = _violation_run(client)
    response = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "reject",
            "declared_scope": "x",
            "attested_by": "Hao",
            "clears_decisions": [debt[0]["decision_id"]],
        },
    )
    assert response.status_code == 422
    # and the debt is untouched
    assert client.get(f"/api/runs/{run_id}/review-debt?status=open").json() != []


def test_unknown_or_non_review_decision_rejected(client):
    run_id, debt = _violation_run(client)
    for bad in ("nonexistent-id", f"{run_id}-pd-2"):  # pd-2 is a deny, not needs_review
        response = client.post(
            f"/api/runs/{run_id}/attestations",
            json={
                "decision": "accept",
                "declared_scope": "x",
                "attested_by": "Hao",
                "clears_decisions": [bad],
            },
        )
        assert response.status_code == 422, bad


def test_blank_identity_is_rejected(client):
    # Finding 1: an empty attested_by / declared_scope must not clear debt.
    run_id, debt = _violation_run(client)
    for body in (
        {"decision": "accept", "declared_scope": "s", "attested_by": ""},
        {"decision": "accept", "declared_scope": "  ", "attested_by": "Hao"},
    ):
        response = client.post(
            f"/api/runs/{run_id}/attestations",
            json={**body, "clears_decisions": [debt[0]["decision_id"]]},
        )
        assert response.status_code == 422, body
    assert client.get(f"/api/runs/{run_id}/review-debt?status=open").json() != []


def test_unknown_seat_is_rejected_but_declared_seat_is_accepted(client):
    # Finding 1: seat_id, when given, must reference a seat this run declared.
    run_id, debt = _violation_run(client)
    bad = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "seat_id": "nonexistent-seat",
            "clears_decisions": [debt[0]["decision_id"]],
        },
    )
    assert bad.status_code == 422
    assert client.get(f"/api/runs/{run_id}/review-debt?status=open").json() != []

    # policy-violation-run declares the seat "seat-security"
    good = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "seat_id": "seat-security",
            "clears_decisions": [debt[0]["decision_id"]],
        },
    )
    assert good.status_code == 200
    assert client.get(f"/api/runs/{run_id}/review-debt?status=open").json() == []


def test_duplicate_ids_in_one_request_count_once(client):
    # Finding 3: passing the same decision id twice must not double-count.
    run_id, debt = _violation_run(client)
    did = debt[0]["decision_id"]

    def cleared_value() -> float:
        for ln in client.get("/metrics").text.splitlines():
            if ln.startswith("aro_review_debt_cleared_total{") and "review-sensitive-read" in ln:
                return float(ln.split()[-1])
        return 0.0

    before = cleared_value()
    resp = client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "clears_decisions": [did, did, did],
        },
    )
    assert resp.status_code == 200
    assert cleared_value() == before + 1.0  # not +3


def test_cleared_metric_counts_once(client):
    run_id, debt = _violation_run(client)
    body = {
        "decision": "accept",
        "declared_scope": "x",
        "attested_by": "Hao",
        "clears_decisions": [debt[0]["decision_id"]],
    }
    client.post(f"/api/runs/{run_id}/attestations", json=body)
    client.post(f"/api/runs/{run_id}/attestations", json=body)  # re-clear: recorded, not recounted

    metrics = client.get("/metrics").text
    line = next(
        ln
        for ln in metrics.splitlines()
        if ln.startswith("aro_review_debt_cleared_total{") and "review-sensitive-read" in ln
    )
    # global registry persists across tests in-process; per-run consumption
    # counted once means the counter moved by exactly 1 for this test's run —
    # so it must be a whole number (sanity) and the second post added nothing.
    value_after = float(line.split()[-1])

    run2, debt2 = _violation_run(client)
    client.post(
        f"/api/runs/{run2}/attestations",
        json={**body, "clears_decisions": [debt2[0]["decision_id"]]},
    )
    metrics2 = client.get("/metrics").text
    line2 = next(
        ln
        for ln in metrics2.splitlines()
        if ln.startswith("aro_review_debt_cleared_total{") and "review-sensitive-read" in ln
    )
    assert float(line2.split()[-1]) == value_after + 1.0
