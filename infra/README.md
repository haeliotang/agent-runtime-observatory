# Infra

Local observability stack for the Agent Runtime Observatory.

## Run it

From this directory:

```sh
docker compose up --build
```

What runs where:

| Service    | URL                    | Notes                                    |
| ---------- | ---------------------- | ---------------------------------------- |
| api        | http://localhost:8000  | FastAPI (`/api/*`, `/healthz`, `/metrics`), healthchecked |
| postgres   | (internal)             | Run store + work queue (`FOR UPDATE SKIP LOCKED` claims) |
| prometheus | http://localhost:9090  | Scrapes api:8000 and worker:9100 every 5s |
| grafana    | http://localhost:3000  | Anonymous access (Admin), dashboard "Agent Runtime Observatory" pre-provisioned |

The worker runs in the background (no host port for its work; metrics on `worker:9100` inside the compose network). API and worker use Postgres via `ARO_DATABASE_URL` (drop that env var to fall back to SQLite) and share the `aro-data` volume at `/data` for trace files.

Worker resilience knobs: `ARO_MAX_ATTEMPTS` (default 3), `ARO_RETRY_BACKOFF_S`
(default 2, doubles per attempt), and `ARO_CHAOS_FAIL_ATTEMPTS=N` to inject
deterministic failures and watch retries/dead-letters flow through
`aro_queue_retries_total` / `aro_queue_dead_letters_total` and
`GET localhost:8000/api/queue?status=dead`. Run creation is rate-limited by
`ARO_RATE_LIMIT_PER_MINUTE` (default 120).

## Generate traffic

```sh
curl -X POST localhost:8000/api/runs \
  -H 'content-type: application/json' \
  -d '{"example":"policy-violation-run"}'
```

Repeat a few times (also try `coding-agent-run` and `document-research-run`), then watch the panels in Grafana:

![Grafana dashboard](../docs/assets/grafana-dashboard.png)

## Kubernetes (optional)

`k8s/` contains minimal reference manifests (namespace `aro`, api Deployment + Service, worker Deployment). They use per-pod `emptyDir` storage, so they are illustrative rather than production-ready:

```sh
kubectl apply -k infra/k8s
```
