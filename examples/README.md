# Examples

Each directory is one self-contained scenario: the unit of demo, eval, and
regression.

```
<name>/
├── script.json          # Task, Goal, ReviewerSeats, and the step list
├── policy.yaml          # declarative rule bundle gating every step
├── workspace/           # files the run sees (copied into memory, never mutated on disk)
├── expected.json        # what a correct run looks like (aro_evals asserts this)
└── golden/trace.jsonl   # recorded reference trace (replay regression asserts zero divergence)
```

| Example | What it demonstrates |
|---|---|
| `coding-agent-run` | happy path: red tests → patch → green tests → report artifact |
| `document-research-run` | allowlisted fetch → deterministic summary → notes artifact |
| `policy-violation-run` | compromised-agent scenario: sensitive read flagged `needs_review`, two exfiltration attempts denied, everything recorded |

Run one directly:

```bash
uv run python -c "
from pathlib import Path
from aro_runtime import load_example, run_example
run = run_example(load_example(Path('examples/policy-violation-run')))
print(run.status, [d.decision for d in run.policy_decisions])
"
```

Regenerate golden traces after an intentional behavior change:

```bash
uv run python scripts/record_goldens.py
```
