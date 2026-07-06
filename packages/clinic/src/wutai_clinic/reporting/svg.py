"""SVG generation helpers for wutai_clinic HTML reports.

All functions return raw SVG strings (no external dependencies).
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

# ── colour palette ──────────────────────────────────────────────────────────
LABEL_COLORS: dict[str, str] = {
    "both_unresolved_trigger_hit_pair_no_uplift": "#9e9e9e",
    "no_uplift": "#9e9e9e",
    "uplift": "#4caf50",
    "harm": "#f44336",
    "unparsed": "#ffeb3b",
}
DEFAULT_CELL_COLOR = "#b0bec5"

MANIFEST_NODE_COLOR = "#90caf9"  # has manifest
NO_MANIFEST_NODE_COLOR = "#ef9a9a"  # no manifest
EDGE_COLOR = "#78909c"


def _label_color(effect_label: str) -> str:
    for key, color in LABEL_COLORS.items():
        if key in effect_label:
            return color
    return DEFAULT_CELL_COLOR


# ── Pair Outcome Matrix SVG ──────────────────────────────────────────────────


def pair_matrix_svg(pairs: list[dict[str, Any]]) -> str:
    """Return an SVG showing stratum × instance grid coloured by effect_label."""
    strata_order = ["v0_reference", "v1", "v2_strict_fresh", "v2_reference"]
    # group by stratum then instance_id
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs:
        by_stratum[p.get("protocol_stratum", "unknown")].append(p)

    all_instances: list[str] = []
    seen: set[str] = set()
    for s in strata_order:
        for p in by_stratum.get(s, []):
            iid = p.get("instance_id", "?")
            if iid not in seen:
                all_instances.append(iid)
                seen.add(iid)
    for s, ps in by_stratum.items():
        if s not in strata_order:
            for p in ps:
                iid = p.get("instance_id", "?")
                if iid not in seen:
                    all_instances.append(iid)
                    seen.add(iid)

    # Only render strata present plus a "Summary" row
    active_strata = [s for s in strata_order if s in by_stratum]
    if not active_strata:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='60'><text x='10' y='30' font-size='14'>No pair outcomes found.</text></svg>"

    col_w = max(110, 700 // (len(all_instances) + 1))
    row_h = 36
    label_w = 160
    header_h = 50
    width = label_w + col_w * len(all_instances) + 20
    height = header_h + row_h * (len(active_strata) + 1) + 20  # +1 for summary

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'style="font-family:monospace,sans-serif;font-size:11px;">'
    )

    # column headers (instance ids)
    for ci, inst in enumerate(all_instances):
        x = label_w + ci * col_w + col_w // 2
        short = inst[-20:] if len(inst) > 20 else inst
        lines.append(
            f'<text x="{x}" y="20" text-anchor="middle" font-size="9" '
            f'transform="rotate(-35,{x},20)">{html.escape(short)}</text>'
        )

    # summary counts per label
    label_totals: dict[str, int] = defaultdict(int)
    for p in pairs:
        label_totals[p.get("effect_label", "unparsed")] += 1

    # Summary row at top
    summary_y = header_h
    lines.append(f'<text x="5" y="{summary_y + row_h // 2 + 4}" font-weight="bold">Summary</text>')
    summary_txt = "  ".join(f"{v}×{html.escape(k[:18])}" for k, v in sorted(label_totals.items()))
    lines.append(
        f'<text x="{label_w}" y="{summary_y + row_h // 2 + 4}" font-size="10">'
        f"{html.escape(summary_txt)}</text>"
    )
    lines.append(
        f'<rect x="1" y="{summary_y}" width="{width - 2}" height="{row_h}" '
        f'fill="#e3f2fd" stroke="#90caf9" stroke-width="1" rx="2"/>'
    )
    # re-draw text on top of rect
    lines.append(f'<text x="5" y="{summary_y + row_h // 2 + 4}" font-weight="bold">Summary</text>')
    lines.append(
        f'<text x="{label_w}" y="{summary_y + row_h // 2 + 4}" font-size="10">'
        f"{html.escape(summary_txt)}</text>"
    )

    # data rows
    for ri, stratum in enumerate(active_strata):
        row_y = header_h + row_h * (ri + 1)
        lines.append(
            f'<text x="5" y="{row_y + row_h // 2 + 4}" font-size="10">{html.escape(stratum)}</text>'
        )
        # index pairs in this stratum by instance
        stratum_pairs = {p.get("instance_id", "?"): p for p in by_stratum[stratum]}
        for ci, inst in enumerate(all_instances):
            cx = label_w + ci * col_w
            p = stratum_pairs.get(inst)
            if p is None:
                fill = "#f5f5f5"
                tooltip = "no data"
                label_short = ""
            else:
                el = p.get("effect_label", "unparsed")
                fill = _label_color(el)
                source = html.escape(str(p.get("source_path", "")))
                pair_id = html.escape(str(p.get("pair_id", "")))
                tooltip = f"{el} | pair: {pair_id} | {source}"
                label_short = "✓" if "no_uplift" in el else ("↑" if "uplift" in el else "!")
            lines.append(
                f'<rect x="{cx}" y="{row_y}" width="{col_w - 2}" height="{row_h - 2}" '
                f'fill="{fill}" stroke="#ccc" stroke-width="1" rx="2">'
                f"<title>{tooltip}</title></rect>"
            )
            if label_short:
                lines.append(
                    f'<text x="{cx + col_w // 2}" y="{row_y + row_h // 2 + 4}" '
                    f'text-anchor="middle" font-size="12">{label_short}</text>'
                )

    lines.append("</svg>")
    return "\n".join(lines)


# ── Evidence DAG SVG ─────────────────────────────────────────────────────────


def dag_svg(dag: dict[str, Any], truncated: bool = False, truncated_count: int = 0) -> str:
    """Render a layered directed graph SVG from the DAG dict."""
    nodes: list[dict[str, Any]] = dag.get("nodes", [])
    edges: list[dict[str, Any]] = dag.get("edges", [])

    if not nodes:
        return (
            "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='60'>"
            "<text x='10' y='30' font-size='14'>No DAG nodes.</text></svg>"
        )

    # Assign layers by directory depth
    def _depth(path_str: str) -> int:
        return path_str.count("/")

    depths = {n["id"]: _depth(n["id"]) for n in nodes}
    min_depth = min(depths.values()) if depths else 0
    layers: dict[int, list[str]] = defaultdict(list)
    for nid, d in depths.items():
        layers[d - min_depth].append(nid)

    max_layer = max(layers.keys()) if layers else 0
    layer_count = max_layer + 1

    node_w = 140
    node_h = 24
    h_gap = 30
    v_gap = 18
    margin = 20

    max_in_layer = max(len(v) for v in layers.values()) if layers else 1
    svg_w = layer_count * (node_w + h_gap) + margin * 2
    svg_h = max_in_layer * (node_h + v_gap) + margin * 2 + (40 if truncated else 0)

    # positions
    positions: dict[str, tuple[int, int]] = {}
    for layer_idx in range(layer_count):
        layer_nodes = layers[layer_idx]
        total_h = len(layer_nodes) * (node_h + v_gap) - v_gap
        start_y = (svg_h - total_h) // 2
        x = margin + layer_idx * (node_w + h_gap)
        for ni, nid in enumerate(layer_nodes):
            y = start_y + ni * (node_h + v_gap)
            positions[nid] = (x, y)

    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" '
        f'style="font-family:monospace,sans-serif;font-size:9px;">'
    )

    if truncated:
        lines.append(
            f'<text x="{margin}" y="15" font-size="11" fill="#b71c1c">'
            f"[Truncated: showing pair-related subgraph only; "
            f"{truncated_count} nodes omitted]</text>"
        )

    # draw edges
    for e in edges:
        src = e.get("source", "")
        dst = e.get("target", "")
        if src in positions and dst in positions:
            sx, sy = positions[src]
            dx, dy = positions[dst]
            x1 = sx + node_w
            y1 = sy + node_h // 2
            x2 = dx
            y2 = dy + node_h // 2
            mid_x = (x1 + x2) // 2
            lines.append(
                f'<path d="M{x1},{y1} C{mid_x},{y1} {mid_x},{y2} {x2},{y2}" '
                f'fill="none" stroke="{EDGE_COLOR}" stroke-width="1" marker-end="url(#arr)"/>'
            )

    # arrowhead marker
    lines.insert(
        1,
        '<defs><marker id="arr" markerWidth="6" markerHeight="6" '
        'refX="5" refY="3" orient="auto">'
        f'<path d="M0,0 L6,3 L0,6 Z" fill="{EDGE_COLOR}"/>'
        "</marker></defs>",
    )

    # draw nodes
    for n in nodes:
        nid = n["id"]
        if nid not in positions:
            continue
        x, y = positions[nid]
        has_manifest = n.get("has_manifest", False)
        fill = MANIFEST_NODE_COLOR if has_manifest else NO_MANIFEST_NODE_COLOR
        short_name = nid.split("/")[-1]
        if len(short_name) > 20:
            short_name = short_name[:18] + ".."
        tooltip = html.escape(nid)
        lines.append(
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" '
            f'fill="{fill}" stroke="#546e7a" stroke-width="1" rx="3">'
            f"<title>{tooltip}</title></rect>"
        )
        lines.append(
            f'<text x="{x + 5}" y="{y + node_h // 2 + 4}" font-size="9">'
            f"{html.escape(short_name)}</text>"
        )

    lines.append("</svg>")
    return "\n".join(lines)
