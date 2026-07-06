"""Guards for the intervention-verdict credibility artifact.

These lock in the three properties that make the demo trustworthy: it is grounded
in real reports, it fails fast (never silently drops a row), and the README table
cannot drift from the script output.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "run_intervention_verdict_demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("verdict_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


def test_rows_grounded_in_real_reports():
    rows, prov = demo.build_rows(demo.DEFAULT_MODELS_ROOT)

    v2 = next(r for r in rows if r["intervention"].startswith("v2 constraint"))
    assert v2["outcome"] == "4 strict-fresh pairs, 0 uplift"
    assert v2["verdict"].startswith("calibrated null")

    pos = next(r for r in rows if "positive control" in r["intervention"])
    assert pos["verdict"] == "true positive - sensitivity anchor"

    sweep = next(r for r in rows if "oracle-probe sweep" in r["intervention"])
    assert "unmoved" in sweep["outcome"]  # the honest sensitivity caveat row

    v2p = next(p for p in prov if p["row"] == "v2")
    assert v2p["sha256"] and v2p["source"] and v2p["decision"]


def test_fail_fast_on_missing_reports(tmp_path):
    # An empty models root must raise, not silently emit a short table.
    with pytest.raises(demo.MissingArtifact):
        demo.build_rows(tmp_path)


@pytest.mark.parametrize("doc", ["README.md", "MEMO.md"])
def test_doc_block_in_sync(doc):
    rows, _ = demo.build_rows(demo.DEFAULT_MODELS_ROOT)
    expected = demo.render_block(rows)
    actual = demo.extract_block((REPO / doc).read_text())
    assert actual is not None, f"generated-block markers missing from {doc}"
    assert actual.strip() == expected.strip(), (
        f"{doc} table is stale; re-run scripts/run_intervention_verdict_demo.py"
    )
