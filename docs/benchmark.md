# Benchmark

What the observability substrate costs, measured, not asserted.

## Method

50 iterations per example per mode on a single Apple M-series machine
(Python 3.12, local SQLite, no network). Modes:

- **bare** — `run_example()` with no trace file and no hooks;
- **traced** — same run recording the full JSONL trace (flushed per event);
- **replay** — `replay_trace()` of a recorded trace, including fresh
  re-execution and per-step comparison.

Reproduce with the snippet at the bottom of this file.

## Results (v0.1)

| Example | Steps | bare median | traced median | replay median | trace overhead |
|---|---|---|---|---|---|
| coding-agent-run | 5 | 0.17 ms | 0.45 ms | 0.27 ms | ~0.28 ms (~0.06 ms/step) |
| document-research-run | 3 | 0.20 ms | 0.45 ms | 0.30 ms | ~0.25 ms |
| policy-violation-run | 5 | 0.17 ms | 0.49 ms | 0.29 ms | ~0.32 ms |

p95 stayed under 0.75 ms in every mode.

## Reading the numbers honestly

- The absolute numbers are small because the tools are in-memory simulations;
  a real agent's step latency is dominated by model and network calls
  measured in seconds. The relevant claim is **relative**: full evidence
  recording (digests, policy decisions, JSONL trace with per-event flush)
  costs fractions of a millisecond per step — three to four orders of
  magnitude below real step latency. "We can't afford to record everything"
  is not supported by this workload shape.
- Replay is cheaper than recording here because it skips file writes; against
  real tools, replay cost is dominated by whatever re-execution costs, which
  is why the replay engine verifies digests rather than requiring live
  side effects.
- No load testing yet: SQLite + single worker is the known v0.1 ceiling.
  Queue throughput and Postgres/Redis are tracked as a roadmap issue.

## Reproduce

```bash
uv run python - <<'EOF'
import statistics, tempfile, time
from pathlib import Path
from aro_runtime import discover_examples, run_example, replay_trace, Workspace

examples = discover_examples(Path("examples"))
N = 50
for name, ex in examples.items():
    bare, traced, rp = [], [], []
    for _ in range(N):
        t0 = time.perf_counter(); run_example(ex); bare.append((time.perf_counter()-t0)*1e3)
    with tempfile.TemporaryDirectory() as td:
        for i in range(N):
            t0 = time.perf_counter()
            run_example(ex, trace_path=Path(td)/f"{i}.jsonl")
            traced.append((time.perf_counter()-t0)*1e3)
        for i in range(N):
            t0 = time.perf_counter()
            replay_trace(Path(td)/"0.jsonl", Workspace.from_dir(ex.workspace_dir))
            rp.append((time.perf_counter()-t0)*1e3)
    print(name, [f"{statistics.median(xs):.2f}ms" for xs in (bare, traced, rp)])
EOF
```
