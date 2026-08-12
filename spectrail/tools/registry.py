from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from spectrail.agent.models import ToolResult, ToolSpec
from spectrail.tools.base import AgentExecutionContext, AgentTool


class ToolRegistryError(ValueError):
    pass


class DuplicateToolError(ToolRegistryError):
    pass


class ToolNotFoundError(ToolRegistryError):
    pass


class ToolContractError(ToolRegistryError):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[AgentTool] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._argument_models: dict[str, type] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            raise DuplicateToolError(f"duplicate tool name: {tool.name}")
        try:
            input_schema = tool.arguments_model.model_json_schema()
            if input_schema.get("additionalProperties") is not False:
                raise ToolContractError(
                    f"TOOL_ARGUMENTS_MUST_FORBID_EXTRA_FIELDS: {tool.name}"
                )
            if tool.output_schema_version != "agent_tool_result_v1":
                raise ToolContractError(
                    f"TOOL_OUTPUT_SCHEMA_UNSUPPORTED: {tool.name}"
                )
            spec = ToolSpec(
                name=tool.name,
                description=tool.description,
                side_effects=tool.side_effects,
                input_schema_version=tool.input_schema_version,
                input_schema=input_schema,
                output_schema_version=tool.output_schema_version,
            )
        except (AttributeError, ValidationError) as exc:
            raise ToolContractError(f"TOOL_SPEC_INVALID: {tool.name}") from exc
        self._tools[tool.name] = tool
        self._specs[tool.name] = spec
        self._argument_models[tool.name] = tool.arguments_model

    def get_spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name].model_copy(deep=True)
        except KeyError as exc:
            raise ToolNotFoundError(f"unknown tool: {name}") from exc

    def specs(self) -> list[ToolSpec]:
        return [
            self._specs[name].model_copy(deep=True)
            for name in sorted(self._specs)
        ]

    def invoke(
        self,
        name: str,
        context: AgentExecutionContext,
        arguments: dict[str, Any],
    ) -> ToolResult:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"unknown tool: {name}") from exc
        validated_arguments = self.validate_arguments(name, arguments)

        raw_result = tool.invoke(context, validated_arguments)
        try:
            result = ToolResult.model_validate(raw_result)
        except ValidationError as exc:
            raise ToolContractError(f"TOOL_RESULT_INVALID: {name}") from exc
        if result.tool != name:
            raise ToolContractError(
                f"TOOL_RESULT_NAME_MISMATCH: expected {name}, got {result.tool}"
            )
        return result

    def validate_arguments(
        self,
        name: str,
        arguments: dict[str, Any],
    ):
        if name not in self._tools:
            raise ToolNotFoundError(f"unknown tool: {name}")
        try:
            return self._argument_models[name].model_validate(arguments)
        except ValidationError as exc:
            raise ToolContractError(f"TOOL_ARGUMENTS_INVALID: {name}") from exc
