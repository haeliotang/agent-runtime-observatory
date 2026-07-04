"""CLI: ``python -m aro_evals [examples_dir]`` — exit 1 on any failure."""

import sys
from pathlib import Path

from aro_evals.golden import evaluate_all


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples")
    results = evaluate_all(root)
    if not results:
        print(f"no examples found under {root}")
        return 1
    failed = 0
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.example}")
        for failure in result.failures:
            print(f"       - {failure}")
        failed += 0 if result.passed else 1
    print(f"\n{len(results) - failed}/{len(results)} examples passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
