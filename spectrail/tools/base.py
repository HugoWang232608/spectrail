from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from spectrail.agent.models import DocumentProfile, ToolResult
from spectrail.agent.policy import AgentPolicy
from spectrail.evidence.models import EvidenceIndex
from spectrail.parsers.base import ParsedDocument


@dataclass(frozen=True)
class AgentExecutionContext:
    task_id: str
    run_generation: int
    task_dir: Path
    document_path: Path
    policy: AgentPolicy
    parsed_document: ParsedDocument
    evidence_index: EvidenceIndex
    document_profile: DocumentProfile


ArgumentsT = TypeVar("ArgumentsT", bound=BaseModel)


class AgentTool(Protocol[ArgumentsT]):
    name: str
    description: str
    side_effects: Literal["none", "task_artifacts"]
    input_schema_version: str
    output_schema_version: str
    arguments_model: type[ArgumentsT]

    def invoke(
        self,
        context: AgentExecutionContext,
        arguments: ArgumentsT,
    ) -> ToolResult:
        ...
