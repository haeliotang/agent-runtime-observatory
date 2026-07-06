"""HTML report generation for wutai_clinic evidence artifacts.

Single-file, zero external dependencies. All styles inline, all graphics
inline SVG.  Read-only renderer: never writes to evidence root.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from wutai_clinic.io.report import utc_now
from wutai_clinic.reporting.svg import dag_svg, pair_matrix_svg

# ── Fixed claim banner ───────────────────────────────────────────────────────
CLAIM_BANNER = (
    "This report renders existing audited evidence artifacts read-only. "
    "It introduces no new claims: all completed intervention pairs to date "
    "are no-uplift, and nothing on this page implies generalized uplift, "
    "predictive diagnosis, or causal effect."
)

# ── Strata detection ─────────────────────────────────────────────────────────
_STRATUM_RULES: list[tuple[str, str]] = [
    ("protocol_v2_official_eval", "v2_strict_fresh"),
    ("protocol_v2_reference", "v2_reference"),
    ("protocol_v1_fresh_official_eval", "v1"),
    ("protocol_v1_official_eval", "v1"),
    ("four_pair_official_eval", "v0_reference"),
    ("phase6_official_eval", "v0_reference"),
]


def _infer_stratum(path: Path) -> str:
    path_str = str(path)
    for token, stratum in _STRATUM_RULES:
        if token in path_str:
            return stratum
    return "v0_reference"


def _infer_instance(path: Path, data: dict[str, Any]) -> str:
    # prefer explicit field
    for key in ("source_task_id", "instance_id"):
        val = data.get(key)
        if val:
            return str(val)
    # fall back to parent directory name
    return path.parent.name


# ── collect_pair_outcomes ────────────────────────────────────────────────────


def collect_pair_outcomes(root: Path) -> list[dict[str, Any]]:
    """Scan root for official-eval report/scorecard/summary files.

    Returns a list of dicts with keys:
      protocol_stratum, instance_id, pair_id, effect_label, decision, source_path

    Files that fail to parse are collected in an ``_unparsed`` entry appended
    at the end; they never raise.
    """
    patterns = [
        "*official_eval*report*.json",
        "*dual_scorecard*.json",
        "*pair_summary*.json",
        "*pair_summary*.jsonl",
        "*four_pair*summary*.json",
    ]
    candidates: list[Path] = []
    seen_paths: set[Path] = set()
    for pattern in patterns:
        for p in sorted(root.rglob(pattern)):
            if p not in seen_paths:
                candidates.append(p)
                seen_paths.add(p)

    outcomes: list[dict[str, Any]] = []
    unparsed: list[dict[str, Any]] = []

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            unparsed.append({"source_path": str(path), "error": "read_error"})
            continue

        # handle JSONL: take first line
        if path.suffix == ".jsonl":
            first = text.split("\n")[0].strip()
            if not first:
                continue
            text = first

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            unparsed.append({"source_path": str(path), "error": str(exc)})
            continue

        if not isinstance(data, dict):
            unparsed.append({"source_path": str(path), "error": "not_a_dict"})
            continue

        # Extract fields — missing key → None, never raise
        effect_label = data.get("effect_label")
        decision = data.get("decision")

        # Per-pair summary files may have nested per_pair list
        if effect_label is None and "per_pair" in data:
            for pp in data.get("per_pair", []):
                if not isinstance(pp, dict):
                    continue
                el = pp.get("effect_label")
                if el is None:
                    continue
                outcomes.append(
                    {
                        "protocol_stratum": _infer_stratum(path),
                        "instance_id": pp.get("source_task_id") or path.parent.name,
                        "pair_id": pp.get("pair_id", ""),
                        "effect_label": el,
                        "decision": pp.get("decision", ""),
                        "source_path": str(path),
                    }
                )
            continue

        if effect_label is None:
            # Skip files that simply don't carry this key (not a pair report)
            continue

        outcomes.append(
            {
                "protocol_stratum": _infer_stratum(path),
                "instance_id": _infer_instance(path, data),
                "pair_id": data.get("pair_id", ""),
                "effect_label": str(effect_label),
                "decision": str(decision) if decision is not None else "",
                "source_path": str(path),
            }
        )

    if unparsed:
        outcomes.append(
            {
                "protocol_stratum": "unparsed",
                "instance_id": "_unparsed",
                "pair_id": "",
                "effect_label": "unparsed",
                "decision": "",
                "source_path": "",
                "_unparsed_files": unparsed,
            }
        )

    return outcomes


# ── collect_evidence_dag ─────────────────────────────────────────────────────
_PAIR_TOKENS = (
    "official_eval",
    "dual_scorecard",
    "pair_summary",
    "four_pair",
    "live_pair",
    "manifest",
)

MAX_DAG_NODES = 150


def _is_pair_related(path_str: str) -> bool:
    lower = path_str.lower()
    return any(t in lower for t in _PAIR_TOKENS)


def collect_evidence_dag(root: Path) -> dict[str, Any]:
    """Build a DAG of evidence files.

    Nodes = scanned JSON/JSONL files under root.
    Edges = substring matches where a file's path string appears inside
    another file's JSON text (labeled edge_basis: "path_reference").

    If >150 nodes, only pair-related nodes are returned and a truncation
    note is included.
    """
    all_files = sorted(p for p in root.rglob("*.json") if p.is_file()) + sorted(
        p for p in root.rglob("*.jsonl") if p.is_file()
    )

    # Dedup
    all_files = list(dict.fromkeys(all_files))

    truncated = False
    truncated_count = 0
    if len(all_files) > MAX_DAG_NODES:
        truncated_count = len(all_files) - sum(1 for f in all_files if _is_pair_related(str(f)))
        all_files = [f for f in all_files if _is_pair_related(str(f))]
        truncated = True

    # has_manifest heuristic: file is a manifest OR sibling manifest exists
    manifest_dirs: set[Path] = set()
    for f in all_files:
        if "manifest" in f.name.lower():
            manifest_dirs.add(f.parent)

    nodes: list[dict[str, Any]] = []
    for f in all_files:
        nodes.append(
            {
                "id": str(f),
                "name": f.name,
                "has_manifest": ("manifest" in f.name.lower() or f.parent in manifest_dirs),
            }
        )

    # Build edge map from path references
    # Read each file once; look for other file paths inside its text
    file_texts: dict[str, str] = {}
    for f in all_files:
        try:
            file_texts[str(f)] = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            file_texts[str(f)] = ""

    file_ids = {str(f) for f in all_files}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()

    for src_id, text in file_texts.items():
        for tgt_id in file_ids:
            if tgt_id == src_id:
                continue
            # simple substring match on the relative tail (avoid over-matching)
            tgt_name = Path(tgt_id).name
            if len(tgt_name) > 8 and tgt_name in text:
                key = (src_id, tgt_id)
                if key not in seen_edges:
                    seen_edges.add(key)
                    edges.append(
                        {
                            "source": src_id,
                            "target": tgt_id,
                            "edge_basis": "path_reference",
                        }
                    )

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "truncated_count": truncated_count,
    }


# ── build_html ───────────────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: ui-monospace, 'Courier New', monospace; font-size: 13px;
       background: #fafafa; color: #212121; }
#banner { position: sticky; top: 0; z-index: 100;
          background: #1a237e; color: #e8eaf6; padding: 10px 16px;
          font-size: 12px; line-height: 1.5; }
#banner .ts { font-size: 10px; color: #9fa8da; margin-top: 4px; }
#banner .claim { font-weight: bold; }
.section { margin: 20px 16px; }
.section h2 { font-size: 15px; border-bottom: 2px solid #3f51b5;
              padding-bottom: 4px; margin-bottom: 10px; }
.collapsible summary { cursor: pointer; font-size: 13px;
                       color: #3f51b5; padding: 4px 0; }
.legend { display: flex; gap: 12px; flex-wrap: wrap;
          margin-bottom: 8px; font-size: 11px; }
.legend-item { display: flex; align-items: center; gap: 4px; }
.swatch { width: 14px; height: 14px; border-radius: 2px;
          border: 1px solid #ccc; display: inline-block; }
.overflow { overflow-x: auto; }
.note { font-size: 11px; color: #616161; margin-top: 6px; }
"""

_LEGEND_ITEMS = [
    ("#9e9e9e", "no_uplift"),
    ("#4caf50", "uplift"),
    ("#f44336", "harm"),
    ("#ffeb3b", "unparsed"),
    ("#f5f5f5", "no data"),
]


def _legend_html() -> str:
    items = "".join(
        f'<span class="legend-item">'
        f'<span class="swatch" style="background:{color}"></span>'
        f"{html.escape(label)}</span>"
        for color, label in _LEGEND_ITEMS
    )
    return f'<div class="legend">{items}</div>'


def _str_section(analysis: dict[str, Any] | None) -> str:
    if analysis is None:
        return (
            '<div class="section">'
            "<h2>STR View</h2>"
            '<p class="note">Not provided. Pass --analysis to include per-trajectory STR data.</p>'
            "</div>"
        )
    # Render text summary from analysis dict — no external SVG generation required
    esc = html.escape
    rows = []
    for traj_id, metrics in (analysis.get("metrics") or {}).items():
        avg_str = metrics.get("avg_str", "n/a")
        rows.append(f"<tr><td>{esc(str(traj_id))}</td><td>{esc(str(avg_str))}</td></tr>")
    table = (
        "<table border='1' cellpadding='4' style='border-collapse:collapse'>"
        "<tr><th>trajectory_id</th><th>avg_str</th></tr>" + "".join(rows) + "</table>"
    )
    return f'<div class="section"><h2>STR View</h2>{table}</div>'


def build_html(
    pairs: list[dict[str, Any]],
    dag: dict[str, Any],
    analysis: dict[str, Any] | None = None,
    title: str = "Wutai Clinic Evidence Report",
) -> str:
    """Return a complete standalone HTML string."""
    now = utc_now()
    esc = html.escape

    # Section A: Pair Outcome Matrix
    matrix_svg = pair_matrix_svg(pairs)

    # Section B: Evidence DAG
    truncated = dag.get("truncated", False)
    truncated_count = dag.get("truncated_count", 0)
    evidence_dag_svg = dag_svg(dag, truncated=truncated, truncated_count=truncated_count)

    # Section C: STR View
    str_section = _str_section(analysis)

    # Pair table (textual fallback / details)
    pair_rows = ""
    for p in pairs:
        if p.get("instance_id") == "_unparsed":
            continue
        el = esc(p.get("effect_label", ""))
        stratum = esc(p.get("protocol_stratum", ""))
        inst = esc(p.get("instance_id", ""))
        pid = esc(p.get("pair_id", ""))
        dec = esc(p.get("decision", ""))
        src = esc(p.get("source_path", ""))
        pair_rows += (
            f"<tr><td>{stratum}</td><td>{inst}</td><td>{pid}</td>"
            f"<td>{el}</td><td>{dec}</td>"
            f'<td style="font-size:9px;word-break:break-all">{src}</td></tr>'
        )

    pair_table = (
        "<details class='collapsible'><summary>Show pair detail table</summary>"
        "<div class='overflow'>"
        "<table border='1' cellpadding='3' style='border-collapse:collapse;font-size:10px'>"
        "<tr><th>stratum</th><th>instance</th><th>pair_id</th>"
        "<th>effect_label</th><th>decision</th><th>source_path</th></tr>"
        + pair_rows
        + "</table></div></details>"
    )

    # Unparsed warnings
    unparsed_html = ""
    for p in pairs:
        if p.get("instance_id") == "_unparsed":
            files = p.get("_unparsed_files", [])
            items = "".join(f"<li>{esc(str(f))}</li>" for f in files)
            unparsed_html = (
                f'<p class="note" style="color:#b71c1c">'
                f"Warning: {len(files)} file(s) could not be parsed:</p>"
                f"<ul style='font-size:10px;margin-left:20px'>{items}</ul>"
            )
            break

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{esc(title)}</title>
<style>
{_CSS}
</style>
</head>
<body>
<div id="banner">
  <div class="claim">{esc(CLAIM_BANNER)}</div>
  <div class="ts">Generated: {esc(now)} &nbsp;|&nbsp; Scan root: {esc(str(dag.get("scan_root", "")))} &nbsp;|&nbsp; {esc(title)}</div>
</div>

<div class="section">
  <h2>A. Pair Outcome Matrix</h2>
  {_legend_html()}
  <div class="overflow">
{matrix_svg}
  </div>
  {pair_table}
  {unparsed_html}
</div>

<div class="section">
  <h2>B. Evidence DAG</h2>
  <p class="note">
    Nodes: {len(dag.get("nodes", []))} &nbsp;|&nbsp;
    Edges: {len(dag.get("edges", []))} &nbsp;|&nbsp;
    Edge basis: path_reference (filename substring match)
    {' &nbsp;|&nbsp; <strong style="color:#b71c1c">[TRUNCATED: pair-related subgraph only, ' + str(truncated_count) + " nodes omitted]</strong>" if truncated else ""}
  </p>
  <div class="overflow">
{evidence_dag_svg}
  </div>
  <p class="note">
    Node colours: <span style="background:#90caf9;padding:1px 5px;border-radius:2px">blue = has manifest sibling</span>
    &nbsp; <span style="background:#ef9a9a;padding:1px 5px;border-radius:2px">red = no manifest</span>
  </p>
</div>

{str_section}

</body>
</html>"""


# ── write_html_report ────────────────────────────────────────────────────────


def write_html_report(
    root: Path,
    output_path: Path,
    analysis_path: Path | None = None,
) -> dict[str, Any]:
    """Collect evidence, build HTML, and write to output_path.

    Returns a summary dict with keys: output_path, pairs_found,
    nodes_found, edges_found, truncated, generated_at.
    """
    pairs = collect_pair_outcomes(root)
    dag = collect_evidence_dag(root)
    dag["scan_root"] = str(root)

    analysis: dict[str, Any] | None = None
    if analysis_path is not None and analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            analysis = None

    html_text = build_html(pairs=pairs, dag=dag, analysis=analysis)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")

    return {
        "output_path": str(output_path),
        "pairs_found": sum(1 for p in pairs if p.get("instance_id") != "_unparsed"),
        "nodes_found": len(dag.get("nodes", [])),
        "edges_found": len(dag.get("edges", [])),
        "truncated": dag.get("truncated", False),
        "generated_at": utc_now(),
    }
