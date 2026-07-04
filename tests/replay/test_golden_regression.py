"""The regression gate: every example must match expected.json when run fresh
AND replay its committed golden trace with zero divergence."""

import pytest
from aro_evals import evaluate_example
from aro_runtime import discover_examples

EXAMPLES = ["coding-agent-run", "document-research-run", "policy-violation-run"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_golden_regression(examples_dir, name):
    example = discover_examples(examples_dir)[name]
    result = evaluate_example(example)
    assert result.passed, "\n".join(result.failures)
