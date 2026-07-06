from __future__ import annotations


def test_imports_smoke() -> None:
    from wutai_clinic import __version__
    from wutai_clinic import cli
    from wutai_clinic.engine import diagnoser, grammar_gate, pruner, str_calculator
    from wutai_clinic.evidence import chain, registry
    from wutai_clinic.intervention import attribution, hooks, planner
    from wutai_clinic.schemas import (
        ControlledScorecard,
        DualScorecard,
        NativeScorecard,
        Trajectory,
    )

    assert __version__
    assert cli.app.info.name == "wutai-clinic"
    assert diagnoser and grammar_gate and pruner and str_calculator
    assert chain and registry and attribution and hooks and planner
    assert NativeScorecard().passed
    assert not ControlledScorecard().passed
    assert DualScorecard(NativeScorecard(), ControlledScorecard()).to_table()
    assert Trajectory.from_dict({"sft_turns": []}).to_dict()["sft_turns"] == []
