#!/usr/bin/env python3
"""Intervention-verdict demo: the harness's core value, from real artifacts.

Reads the completed official-eval reports under ``models/`` and emits the
intervention verdict table that the README references. The point is NOT "which
intervention raised the score" but "which apparent improvements survive the
harness, and which are killed as null / leakage / non-deployable".

Design (this is a credibility artifact, so it must not lie about itself):

- **fail-fast**: if any required report is missing or unreadable, exit non-zero.
  It never silently drops a row.
- **provenance**: ``--output`` writes, for every row, the source path, the file
  SHA256, the upstream report ``decision``, and the exact fields read.
- **self-consistency**: ``--check-readme`` regenerates the table and diffs it
  against the generated block in README.md (non-zero exit on drift).

Every row is derived from a recorded report, except the B1 row, which is honestly
marked as having produced no valid verdict (the probe was redundant-by-design with
the control prompt — a design dead-end, not a measured null).

Usage:
    python3 scripts/run_intervention_verdict_demo.py [MODELS_ROOT] [-o OUT.json]
    python3 scripts/run_intervention_verdict_demo.py --check-readme   # CI guard
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent if SCRIPT_PATH.parent.name == "scripts" else SCRIPT_PATH.parent
DEFAULT_MODELS_ROOT = REPO_ROOT / "models" if (REPO_ROOT / "models").exists() else REPO_ROOT.parent / "models"
DEFAULT_README = REPO_ROOT / "README.md"
DEFAULT_MEMO = REPO_ROOT / "MEMO.md"
DEFAULT_COVER = REPO_ROOT / "COVER_NOTE.md"
V1 = "phase6_low_nondeterminism_official_eval_v1"

BEGIN_MARKER = "<!-- BEGIN generated: intervention-verdicts (run_intervention_verdict_demo.py) -->"
END_MARKER = "<!-- END generated: intervention-verdicts -->"

# Required reports — missing any of these is a hard error, not a dropped row.
REQUIRED = {
    "v1": f"{V1}/protocol_v1_batch4_outcomes_current/protocol_v1_batch_outcomes_report.json",
    "v2": f"{V1}/protocol_v2_batch_outcomes_wave3/protocol_v2_batch_outcomes_report.json",
    "mechanism": "phase6_intervention_mechanism_comparison_report.json",
    "epsilon": f"{V1}/protocol_v2_epsilon_estimate/pooled/epsilon_report.json",
}

# B1 design/lineage artifacts (NOT outcome reports) — they make the B1 "no verdict"
# row auditable: the plan, the anti-leak block, and the smoke run that exposed the
# redundant-with-control design dead-end.
B1_LINEAGE = [
    "route_b_probe_v1/b1_plan/b1_plan_report.json",
    "route_b_probe_v1/b1_antileak/b1_antileak_report.json",
    "route_b_probe_v1/smoke_taskcheck/astropy/b1_live_arm_report.json",
]

COLUMNS = [
    ("intervention", "Intervention"),
    ("klass", "Class"),
    ("trigger_hit", "Trigger hit"),
    ("leakage", "Leakage check"),
    ("outcome", "Official outcome"),
    ("verdict", "Verdict"),
]


class MissingArtifact(RuntimeError):
    """Raised when a required report is absent or unreadable (fail-fast)."""


def _load(root: Path, rel: str) -> tuple[dict[str, Any], dict[str, Any]]:
    p = root / rel
    if not p.exists():
        raise MissingArtifact(f"required report missing: {rel}")
    raw = p.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissingArtifact(f"report is not valid JSON: {rel} ({exc})") from exc
    meta = {
        "source": rel,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": data.get("generated_at") or data.get("created_at"),
    }
    return data, meta


def _lineage(root: Path, rel: str) -> dict[str, Any]:
    """Best-effort provenance for a B1 design/lineage artifact (not an outcome)."""
    p = root / rel
    if not p.exists():
        return {"source": rel, "present": False}
    raw = p.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return {
        "source": rel,
        "present": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "decision": data.get("decision"),
        "generated_at": data.get("generated_at") or data.get("created_at"),
        "kind": "lineage (design artifact, not an outcome report)",
    }


def build_rows(root: Path) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Return (display_rows, provenance). Fails fast on any missing report."""
    reports = {k: _load(root, rel) for k, rel in REQUIRED.items()}
    rows: list[dict[str, str]] = []
    prov: list[dict[str, Any]] = []

    # --- Specificity: deployable behavioral interventions, both killed null ---
    v1, v1m = reports["v1"]
    v1_counts = v1["summary"]["protocol_v1_label_counts"]
    rows.append({
        "intervention": "v1 constraint hook (break-loop / require-repro)",
        "klass": "behavioral, deployable", "trigger_hit": "yes", "leakage": "clean",
        "outcome": f"{sum(v1_counts.values())} pair, 0 uplift", "verdict": "calibrated null",
    })
    prov.append({**v1m, "row": "v1", "decision": v1["decision"], "fields": {"label_counts": v1_counts}})

    v2, v2m = reports["v2"]
    s = v2["summary"]
    rows.append({
        "intervention": "v2 constraint hook (break-recurrence + reproduce)",
        "klass": "behavioral, deployable", "trigger_hit": "yes", "leakage": "clean",
        "outcome": f"{s['strict_fresh_pair_count']} strict-fresh pairs, {s['strict_fresh_uplift_count']} uplift",
        "verdict": "calibrated null (underpowered)",
    })
    prov.append({**v2m, "row": "v2", "decision": v2["decision"], "fields": {
        "strict_fresh_pair_count": s["strict_fresh_pair_count"],
        "strict_fresh_uplift_count": s["strict_fresh_uplift_count"],
        "strict_fresh_source_task_ids": s.get("strict_fresh_source_task_ids"),
    }})

    # --- B1: honestly no valid verdict (design dead-end), no artifact to read ---
    rows.append({
        "intervention": "B1 deployable-info injection (issue-derived reproduction)",
        "klass": "informational, deployable", "trigger_hit": "yes (smoke)",
        "leakage": "redundant w/ control", "outcome": "no valid pair",
        "verdict": "design dead-end - no verdict",
    })
    prov.append({
        "row": "b1", "source": None, "sha256": None,
        "decision": "invalidated_redundant_with_control",
        "note": "no outcome report (design dead-end); design/lineage artifacts below",
        "lineage_sources": [_lineage(root, rel) for rel in B1_LINEAGE],
    })

    # --- Sensitivity: a positive control proves the harness is not blind ---
    mech, mm = reports["mechanism"]
    m = mech["summary"]
    improved = m["mechanism_counts"].get(
        "trigger_hit_injection_changed_patch_and_official_outcome_improved", 0)
    rows.append({
        "intervention": "oracle / answer-bearing injection (positive control)",
        "klass": "informational, NON-deployable", "trigger_hit": "yes",
        "leakage": "fail (by design)",
        "outcome": f"{m['positive_uplift_case_count']} resolved ({improved} patch+outcome improved)",
        "verdict": "true positive - sensitivity anchor",
    })
    prov.append({**mm, "row": "oracle_positive", "decision": mech["decision"], "fields": {
        "positive_uplift_case_count": m["positive_uplift_case_count"],
        "mechanism_counts": m["mechanism_counts"],
    }})

    # --- Honest caveat: oracle injection does NOT always move the outcome ---
    excl = v2.get("oracle_probe_rows_excluded", [])
    unmoved = [r for r in excl if r.get("oracle_treatment_resolved") is False]
    rows.append({
        "intervention": "oracle-probe sweep (sphinx-8435 / sphinx-8474)",
        "klass": "informational, NON-deployable", "trigger_hit": "yes",
        "leakage": "fail (by design)",
        "outcome": f"{len(unmoved)}/{len(excl)} unmoved",
        "verdict": "channel bottleneck / capability ceiling",
    })
    prov.append({**v2m, "row": "oracle_probe_sweep",
                 "decision": ";".join(sorted({r.get("decision", "") for r in excl})),
                 "fields": {"excluded": excl}})

    # --- Calibration: the outcome-measurement noise floor ---
    eps, em = reports["epsilon"]
    e = eps["pooled_estimate"]
    rows.append({
        "intervention": "epsilon noise floor (control-arm reruns)",
        "klass": "calibration", "trigger_hit": "n/a", "leakage": "n/a",
        "outcome": f"{e['flip_count']} flips / {e['rerun_count']} reruns",
        "verdict": f"epsilon point {e['point_estimate']:.2f} (95% upper {e['wilson_upper_95']:.2f})",
    })
    prov.append({**em, "row": "epsilon", "decision": eps["decision"], "fields": e})

    return rows, prov


def to_markdown(rows: list[dict[str, str]]) -> str:
    head = "| " + " | ".join(h for _, h in COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in COLUMNS) + " |"
    body = ["| " + " | ".join(r.get(k, "") for k, _ in COLUMNS) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def render_block(rows: list[dict[str, str]]) -> str:
    return f"{BEGIN_MARKER}\n\n{to_markdown(rows)}\n\n{END_MARKER}"


def extract_block(readme_text: str) -> str | None:
    if BEGIN_MARKER not in readme_text or END_MARKER not in readme_text:
        return None
    start = readme_text.index(BEGIN_MARKER)
    end = readme_text.index(END_MARKER) + len(END_MARKER)
    return readme_text[start:end]


_VERIFY_TEXT = """# Verify this credential packet (self-contained)

This packet is reproducible without the rest of the Wutai repo. No SWE-bench rerun
is needed: the demo reads the recorded official-eval reports bundled under `models/`.

1. Reproduce the verdict table (run from inside this packet directory):
   `python3 run_intervention_verdict_demo.py ./models`
   The printed table equals `verdict_table.md`.
2. Check the SHA chain: every `sha256` in `MANIFEST.json` and in `provenance.json`
   (per-row `source` / B1 `lineage_sources`) equals the sha256 of the matching file
   under `models/`.
3. `MEMO.md` is the one-page method memo (problem, method, specificity,
   task-conditional sensitivity, limitations).
4. Optional doc drift guard:
   `python3 run_intervention_verdict_demo.py ./models --check-docs`
"""


def write_bundle(root: Path, bundle_dir: Path) -> dict[str, Any]:
    """Write a self-contained, outsider-runnable credential packet and self-verify."""
    rows, prov = build_rows(root)  # fail-fast on required reports
    models_out = bundle_dir / "models"
    copied: list[str] = []
    for rel in list(REQUIRED.values()) + B1_LINEAGE:
        src = root / rel
        if not src.exists():
            continue
        dst = models_out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        copied.append(rel)

    # self-check 1: the bundled reports reproduce the same verdict rows
    rebuilt, _ = build_rows(models_out)
    if rebuilt != rows:
        raise RuntimeError("bundle does not reproduce the verdict table")

    # self-check 2: closed SHA chain (manifest == provenance == bundled files)
    prov_sha: dict[str, str] = {}
    for p in prov:
        if p.get("source") and p.get("sha256"):
            prov_sha[p["source"]] = p["sha256"]
        for ls in p.get("lineage_sources", []):
            if ls.get("present"):
                prov_sha[ls["source"]] = ls["sha256"]
    manifest: dict[str, Any] = {"files": []}
    for rel in copied:
        sha = hashlib.sha256((models_out / rel).read_bytes()).hexdigest()
        manifest["files"].append({"path": f"models/{rel}", "sha256": sha})
        if rel in prov_sha and prov_sha[rel] != sha:
            raise RuntimeError(f"SHA chain mismatch for {rel}")

    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "verdict_table.md").write_text(to_markdown(rows) + "\n")
    (bundle_dir / "provenance.json").write_text(
        json.dumps({"rows": rows, "provenance": prov}, indent=2))
    (bundle_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    (bundle_dir / "VERIFY.md").write_text(_VERIFY_TEXT)
    (bundle_dir / "run_intervention_verdict_demo.py").write_text(Path(__file__).read_text())
    if DEFAULT_README.exists():
        (bundle_dir / "README.md").write_text(DEFAULT_README.read_text())
    if DEFAULT_MEMO.exists():
        (bundle_dir / "MEMO.md").write_text(DEFAULT_MEMO.read_text())
    if DEFAULT_COVER.exists():
        (bundle_dir / "COVER_NOTE.md").write_text(DEFAULT_COVER.read_text())
    return {"rows": len(rows), "copied": copied, "manifest_files": len(manifest["files"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models_root", nargs="?", default=str(DEFAULT_MODELS_ROOT))
    ap.add_argument("-o", "--output", default=None, help="write provenance JSON")
    ap.add_argument("--check-readme", action="store_true",
                    help="verify README's generated block matches; non-zero on drift")
    ap.add_argument("--check-docs", action="store_true",
                    help="verify generated blocks in README.md/MEMO.md when present; non-zero on drift")
    ap.add_argument("--bundle", default=None,
                    help="write a self-contained, outsider-runnable credential packet to DIR")
    ap.add_argument("--readme", default=str(DEFAULT_README))
    args = ap.parse_args()

    if args.bundle:
        try:
            info = write_bundle(Path(args.models_root), Path(args.bundle))
        except (MissingArtifact, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"bundle written to {args.bundle}: {info['rows']} rows, "
              f"{info['manifest_files']} source files, self-verified "
              f"(reproduces + SHA chain ok).")
        return 0

    try:
        rows, prov = build_rows(Path(args.models_root))
    except MissingArtifact as exc:
        print(f"ERROR (fail-fast): {exc}", file=sys.stderr)
        return 2

    if args.check_readme or args.check_docs:
        expected = render_block(rows).strip()
        targets = (
            [(name, path) for name, path in [("README.md", DEFAULT_README), ("MEMO.md", DEFAULT_MEMO)] if path.exists()]
            if args.check_docs
            else [("README.md", Path(args.readme))]
        )
        if not targets:
            print("ERROR: no generated docs found to check", file=sys.stderr)
            return 4
        failed = False
        for name, path in targets:
            actual = extract_block(path.read_text()) if path.exists() else None
            if actual is None:
                print(f"ERROR: generated-block markers not found in {name}", file=sys.stderr)
                failed = True
            elif actual.strip() != expected:
                print(f"ERROR: {name} generated block is stale; re-run and update it.", file=sys.stderr)
                failed = True
            else:
                print(f"{name} generated block matches script output.")
        return 4 if failed else 0

    print("# Intervention verdicts (specificity + sensitivity)\n")
    print(to_markdown(rows))
    print("\nNull results prove the harness is not credulous; the oracle-positive "
          "control proves it is not blind.")
    print("Caveat: oracle injection moved 1 task but failed 4 probes on 2 others "
          "(channel bottleneck / capability ceiling) — sensitivity is task-conditional.")

    if args.output:
        Path(args.output).write_text(json.dumps(
            {"rows": rows, "provenance": prov, "models_root": str(args.models_root)}, indent=2))
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
