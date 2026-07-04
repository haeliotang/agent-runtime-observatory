# Observatory Web UI

React + Vite + TypeScript dashboard for the agent runtime observatory. It lists recorded
agent runs, shows the step timeline with policy decisions, risk signals, and artifacts,
and can trigger runs and deterministic replays against the FastAPI backend.

## Development

```sh
npm install
npm run dev
```

The dev server proxies `/api`, `/healthz`, and `/metrics` to the backend at
`http://localhost:8000`, so start the API first.

## Build

```sh
npm run build
```

Outputs a static bundle to `dist/`, which the FastAPI app can serve directly.
