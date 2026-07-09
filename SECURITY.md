# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's
[security advisories](https://github.com/haeliotang/agent-runtime-observatory/security/advisories/new)
rather than a public issue. Expect an acknowledgement within a few days.

## Supported versions

This is a reference implementation, not a supported product. Security fixes,
if any, land on `main` and in the next tagged release. Only the latest release
(currently the `v0.2.x` line) is maintained.

## Security posture — read this before deploying

**agent-runtime-observatory is a reference-scale substrate. It is not
internet-facing, and its defaults are demo-grade.** Concretely, and by design:

- **The API is unauthenticated.** `aro_api` exposes run creation, replay,
  attestations, traces, and `/metrics` with no authentication or authorization.
- **CORS is open to `http://localhost:5173`** (the Vite dev server) only.
- **Rate limiting is the only built-in guard** (`ARO_RATE_LIMIT_PER_MINUTE`,
  default 120) and it is a coarse fixed-window limiter, not abuse protection.
- **No multi-tenancy, no secrets management, no network policy.** The SQLite/
  Postgres store and the trace files under `ARO_DATA_DIR` are trusted local
  state.

Do **not** expose `aro_api` or the worker directly to untrusted networks.
If you build on this, put an authenticating, authorizing gateway in front and
treat everything here as the trusted interior. This boundary is tracked as gap
**G2** in [docs/evidence-matrix.md](docs/evidence-matrix.md).

## What the substrate does and does not protect

- **Policy gating** blocks or flags tool steps *inside a run* (see
  [docs/threat-model.md](docs/threat-model.md) for what is detected vs.
  prevented). It is not a sandbox: the reference tools are simulated in-process.
- **Traces are tamper-evident, not tamper-proof.** Replay catches edits to a
  recorded trace (`tests/replay/test_tamper_detection.py`), but traces are
  plaintext JSONL and are not signed. Cryptographic signing is a roadmap item.

## Fixtures

The repository ships deliberately **fake, credential-shaped fixtures** so that
governance scenarios have something to trigger on. The clearest example is
[`examples/policy-violation-run/workspace/.env`](examples/policy-violation-run/workspace/.env):
its values are non-functional placeholders labeled `FAKE`, present only so the
`review-sensitive-read` rule has a credential-shaped file to flag. No real
secret is committed to this repository. If you fork it and add real
configuration, add your own `.gitignore` entries first. This is tracked as gap
**G3** in [docs/evidence-matrix.md](docs/evidence-matrix.md).
