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


def _gauge(client, name: str, run_id: str) -> float:
    """Read a per-run review-debt gauge by scoping to a run with exactly one
    known needs_review item. The gauges are store-wide, so we assert deltas /
    single-run isolation via a fresh data dir per test (the `client` fixture)."""
    for ln in client.get("/metrics").text.splitlines():
        if ln.startswith(name + "{") and "review-sensitive-read" in ln:
            return float(ln.split()[-1])
    return 0.0


def _oldest_open_age(client) -> float:
    for ln in client.get("/metrics").text.splitlines():
        if ln.startswith("aro_review_debt_oldest_open_age_seconds "):  # value line, not "# HELP"
            return float(ln.split()[-1])
    return 0.0


def test_gauges_reflect_open_then_cleared(client):
    run_id, debt = _violation_run(client)
    assert _gauge(client, "aro_review_debt_open", run_id) == 1.0
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 0.0
    assert _oldest_open_age(client) > 0.0  # an open item has a real age
    client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "clears_decisions": [debt[0]["decision_id"]],
        },
    )
    assert _gauge(client, "aro_review_debt_open", run_id) == 0.0
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 1.0


def test_duplicate_ids_do_not_double_count(client):
    # Finding 3: same id three times in one request -> cleared gauge is 1, not 3.
    run_id, debt = _violation_run(client)
    client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "clears_decisions": [debt[0]["decision_id"]] * 3,
        },
    )
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 1.0
    assert _gauge(client, "aro_review_debt_open", run_id) == 0.0


def test_concurrent_clears_do_not_double_count(client):
    # Finding 1: 24 concurrent clears of one item -> cleared gauge stays 1.
    import threading

    run_id, debt = _violation_run(client)
    body = {
        "decision": "accept",
        "declared_scope": "s",
        "attested_by": "Hao",
        "clears_decisions": [debt[0]["decision_id"]],
    }
    threads = [
        threading.Thread(target=lambda: client.post(f"/api/runs/{run_id}/attestations", json=body))
        for _ in range(24)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 1.0  # was up to 24 with the counter
    assert _gauge(client, "aro_review_debt_open", run_id) == 0.0


def test_overwriting_a_run_reopens_debt_in_the_gauge(client, examples_dir, tmp_path):
    # Finding 2: after a cleared run is overwritten, the gauge must reopen it —
    # a monotonic counter could not. state -> cleared, then overwrite -> open+stale.
    from aro_runtime import discover_examples, run_example
    from aro_runtime.store import create_store

    run_id, debt = _violation_run(client)
    client.post(
        f"/api/runs/{run_id}/attestations",
        json={
            "decision": "accept",
            "declared_scope": "s",
            "attested_by": "Hao",
            "clears_decisions": [debt[0]["decision_id"]],
        },
    )
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 1.0

    # overwrite the stored run with a fresh execution (new digest)
    store = create_store(sqlite_path=tmp_path / "aro.db")
    ex = discover_examples(examples_dir)["policy-violation-run"]
    store.save_run(run_example(ex, run_id=run_id), ex.script.task, example="policy-violation-run")

    assert _gauge(client, "aro_review_debt_open", run_id) == 1.0
    assert _gauge(client, "aro_review_debt_stale", run_id) == 1.0
    assert _gauge(client, "aro_review_debt_cleared", run_id) == 0.0
