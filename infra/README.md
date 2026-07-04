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
| api        | http://localhost:8000  | FastAPI (`/api/*`, `/healthz`, `/metrics`) |
| prometheus | http://localhost:9090  | Scrapes api:8000 and worker:9100 every 5s |
| grafana    | http://localhost:3000  | Anonymous access (Admin), dashboard "Agent Runtime Observatory" pre-provisioned |

The worker runs in the background (no host port for its work; metrics on `worker:9100` inside the compose network). The API and worker share the `aro-data` volume mounted at `/data`.

## Generate traffic

```sh
curl -X POST localhost:8000/api/runs \
  -H 'content-type: application/json' \
  -d '{"example":"policy-violation-run"}'
```

Repeat a few times (also try `coding-agent-run` and `document-research-run`), then watch the panels in Grafana.

## Kubernetes (optional)

`k8s/` contains minimal reference manifests (namespace `aro`, api Deployment + Service, worker Deployment). They use per-pod `emptyDir` storage, so they are illustrative rather than production-ready:

```sh
kubectl apply -k infra/k8s
```
