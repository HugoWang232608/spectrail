from __future__ import annotations

from pathlib import Path

import pytest

from spectrail.agent.artifacts import (
    AgentArtifactResetError,
    prepare_new_agent_generation,
    reset_pipeline_artifacts_for_agent_retry,
)
from spectrail.task_transactions import task_operation


PIPELINE_DIRS = ("parsed", "extracted", "review", "exports")
PIPELINE_FILES = ("plan.json", "run_manifest.json")


def _populate(task_dir: Path) -> None:
    task_dir.mkdir()
    (task_dir / "task.json").write_text("{}", encoding="utf-8")
    (task_dir / "input").mkdir()
    (task_dir / "input" / "original.md").write_text("# SRS", encoding="utf-8")
    (task_dir / "agent").mkdir()
    (task_dir / "agent" / "trace.jsonl").write_text("trace", encoding="utf-8")
    (task_dir / "sentinel.txt").write_text("keep", encoding="utf-8")
    for name in PIPELINE_DIRS:
        target = task_dir / name
        target.mkdir()
        (target / "artifact.json").write_text("{}", encoding="utf-8")
    for name in PIPELINE_FILES:
        (task_dir / name).write_text("{}", encoding="utf-8")


def test_retry_reset_removes_only_pipeline_artifacts(tmp_path: Path):
    task_dir = tmp_path / "task"
    _populate(task_dir)

    with task_operation(task_dir, "test_retry_reset"):
        reset_pipeline_artifacts_for_agent_retry(task_dir)

    for name in (*PIPELINE_DIRS, *PIPELINE_FILES):
        assert not (task_dir / name).exists()
    assert (task_dir / "task.json").is_file()
    assert (task_dir / "input" / "original.md").is_file()
    assert (task_dir / "agent" / "trace.jsonl").is_file()
    assert (task_dir / "sentinel.txt").read_text(encoding="utf-8") == "keep"


def test_new_generation_reset_also_removes_old_agent_artifacts(tmp_path: Path):
    task_dir = tmp_path / "task"
    _populate(task_dir)

    with task_operation(task_dir, "test_generation_reset"):
        prepare_new_agent_generation(task_dir)

    assert not (task_dir / "agent").exists()
    assert (task_dir / "task.json").is_file()
    assert (task_dir / "input" / "original.md").is_file()
    assert (task_dir / "sentinel.txt").is_file()


@pytest.mark.parametrize("target_name", ["parsed", "run_manifest.json", "agent"])
def test_resets_fail_closed_on_managed_symlink(
    tmp_path: Path,
    target_name: str,
):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("safe", encoding="utf-8")
    (task_dir / target_name).symlink_to(
        outside,
        target_is_directory=target_name != "run_manifest.json",
    )

    with task_operation(task_dir, "test_reset_symlink"):
        with pytest.raises(AgentArtifactResetError, match="SYMLINK"):
            if target_name == "agent":
                prepare_new_agent_generation(task_dir)
            else:
                reset_pipeline_artifacts_for_agent_retry(task_dir)

    assert (outside / "sentinel.txt").read_text(encoding="utf-8") == "safe"


def test_reset_requires_outer_task_transaction(tmp_path: Path):
    with pytest.raises(AgentArtifactResetError, match="TRANSACTION_REQUIRED"):
        reset_pipeline_artifacts_for_agent_retry(tmp_path / "task")
