from pathlib import Path


def test_agent_evaluation_is_a_first_class_ci_gate():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "name: Run Agent orchestration quality gate" in workflow
    assert "python -m spectrail evaluate-agent" in workflow
    assert "eval/agent/cases" in workflow
    assert "--output outputs/agent-evaluation" in workflow
    assert "outputs/agent-evaluation" in workflow.split(
        "name: Upload evaluation reports",
        maxsplit=1,
    )[1]
