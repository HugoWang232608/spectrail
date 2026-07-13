from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from spectrail.chunking.models import ChunkingConfig
from spectrail.llm.request_profile import ModelRequestProfile
from spectrail.evidence.models import EvidencePolicy


@dataclass(frozen=True)
class PipelineConfig:
    model_mode: str = "mock"
    model_name: str | None = None
    recorded_fixture: str | Path | None = None
    request_profile: ModelRequestProfile | None = None
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    validation_policy: Literal["strict", "quarantine"] = "strict"
    evidence_policy: EvidencePolicy = "structured_if_available"
    dump_prompt: bool = False
    insecure: bool = False

    def __post_init__(self) -> None:
        if self.validation_policy not in {"strict", "quarantine"}:
            raise ValueError("validation_policy must be strict or quarantine")
        if self.evidence_policy not in {
            "quote_only",
            "structured_if_available",
            "structured_required",
        }:
            raise ValueError("unsupported evidence_policy")
