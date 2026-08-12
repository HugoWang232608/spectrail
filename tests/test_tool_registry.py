from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from spectrail.agent.models import PlannerObservation, ToolResult
from spectrail.agent.profiler import DocumentProfiler
from spectrail.evidence.index_builder import ensure_evidence_index
from spectrail.parsers import parse_document
from spectrail.tools.base import AgentExecutionContext
from spectrail.tools.document_profile import ProfileDocumentTool
from spectrail.tools.registry import (
    DuplicateToolError,
    ToolContractError,
    ToolNotFoundError,
    ToolRegistry,
)


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class EchoTool:
    name = "echo"
    description = "Return a bounded test metric."
    side_effects = "none"
    input_schema_version = "echo_args_v1"
    output_schema_version = "agent_tool_result_v1"
    arguments_model = EchoArgs

    def __init__(self, *, returned_tool: str = "echo") -> None:
        self.returned_tool = returned_tool
        self.invocations = 0

    def invoke(self, context, arguments: EchoArgs) -> ToolResult:
        self.invocations += 1
        return ToolResult(
            tool=self.returned_tool,
            status="ok",
            summary="echoed",
            metrics={"value": arguments.value},
        )


class TestPolicy:
    schema_version = "agent_policy_v1"


class PermissiveArgs(BaseModel):
    value: int


class PermissiveTool(EchoTool):
    name = "permissive"
    arguments_model = PermissiveArgs


def _context() -> AgentExecutionContext:
    document_path = Path("docs/sample_srs.md")
    parsed = parse_document(document_path)
    evidence_index = ensure_evidence_index(document_path, parsed)
    profile = DocumentProfiler().build(parsed, evidence_index)
    return AgentExecutionContext(
        task_id="task_test",
        run_generation=1,
        task_dir=Path("/tmp/task_test"),
        document_path=document_path,
        policy=TestPolicy(),
        parsed_document=parsed,
        evidence_index=evidence_index,
        document_profile=profile,
    )


def test_registry_derives_input_schema_and_invokes_validated_arguments():
    tool = EchoTool()
    registry = ToolRegistry([tool])

    spec = registry.get_spec("echo")
    result = registry.invoke("echo", _context(), {"value": 7})

    assert spec.input_schema == EchoArgs.model_json_schema()
    assert spec.side_effects == "none"
    assert result.metrics == {"value": 7}
    assert tool.invocations == 1


def test_agent_execution_context_is_frozen():
    context = _context()

    with pytest.raises(FrozenInstanceError):
        context.task_id = "task_other"  # type: ignore[misc]


def test_registry_rejects_duplicate_and_unknown_tools_without_invocation():
    tool = EchoTool()
    registry = ToolRegistry([tool])

    with pytest.raises(DuplicateToolError, match="echo"):
        registry.register(EchoTool())
    with pytest.raises(ToolNotFoundError, match="unknown"):
        registry.invoke("unknown", _context(), {})
    assert tool.invocations == 0


def test_registry_requires_argument_models_to_forbid_extra_fields():
    with pytest.raises(
        ToolContractError,
        match="TOOL_ARGUMENTS_MUST_FORBID_EXTRA_FIELDS",
    ):
        ToolRegistry([PermissiveTool()])


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"value": "not-an-int"},
        {"value": 1, "document_path": "/etc/passwd"},
    ],
)
def test_registry_validates_arguments_before_side_effect(arguments: dict):
    tool = EchoTool()
    registry = ToolRegistry([tool])

    with pytest.raises(ToolContractError, match="TOOL_ARGUMENTS_INVALID"):
        registry.invoke("echo", _context(), arguments)
    assert tool.invocations == 0


def test_registry_rejects_result_claiming_another_tool():
    tool = EchoTool(returned_tool="other")
    registry = ToolRegistry([tool])

    with pytest.raises(ToolContractError, match="TOOL_RESULT_NAME_MISMATCH"):
        registry.invoke("echo", _context(), {"value": 1})


def test_planner_observation_drops_internal_paths_and_free_text():
    result = ToolResult(
        tool="echo",
        status="warning",
        summary="internal diagnostic /tmp/private",
        metrics={"value": 1},
        warning_codes=["TEST_WARNING"],
        artifacts={"manifest": "/tmp/private/run_manifest.json"},
        retryable=True,
    )

    observation = PlannerObservation.from_tool_result(result)

    assert observation.model_dump() == {
        "schema_version": "planner_observation_v1",
        "tool": "echo",
        "status": "warning",
        "metrics": {"value": 1},
        "warning_codes": ["TEST_WARNING"],
        "error_code": None,
        "retryable": True,
    }
    assert "/tmp/private" not in observation.model_dump_json()


def test_planner_observation_rejects_artifact_paths_in_metrics():
    with pytest.raises(ValueError, match="artifact paths"):
        PlannerObservation(
            tool="echo",
            status="ok",
            metrics={"manifest": "/tmp/private/run_manifest.json"},
        )


def test_profile_document_tool_has_no_planner_controlled_arguments():
    context = _context()
    registry = ToolRegistry([ProfileDocumentTool()])

    result = registry.invoke("profile_document", context, {})

    assert result.status == "ok"
    assert result.metrics["block_count"] == context.document_profile.block_count
    with pytest.raises(ToolContractError, match="TOOL_ARGUMENTS_INVALID"):
        registry.invoke(
            "profile_document",
            context,
            {"document_path": "/tmp/other.md"},
        )
