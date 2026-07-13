from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from spectrail.core.models import DocumentBlock


CHUNK_COUNTER_WIDTH = 8
MAX_SUPPORTED_CHUNK_COUNT = 10**CHUNK_COUNTER_WIDTH - 1


@dataclass(frozen=True)
class ChunkingConfig:
    mode: Literal["off", "auto", "force"] = "auto"
    max_rendered_prompt_chars: int = 16000
    overlap_blocks: int = 1
    min_blocks_for_auto: int = 24
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.mode not in {"off", "auto", "force"}:
            raise ValueError("chunking mode must be off, auto, or force")
        if self.max_rendered_prompt_chars < 1000:
            raise ValueError("max_rendered_prompt_chars must be at least 1000")
        if not 0 <= self.overlap_blocks <= 5:
            raise ValueError("overlap_blocks must be between 0 and 5")
        if self.min_blocks_for_auto < 1:
            raise ValueError("min_blocks_for_auto must be at least 1")


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    index: int
    blocks: list[DocumentBlock]
    block_ids: list[str]
    new_block_ids: list[str]
    overlap_block_ids: list[str]
    context_block_ids: list[str]
    section_path: list[str]
    content_chars: int
    rendered_prompt_chars: int
    estimated_tokens: int
    chunk_fingerprint: str
    warnings: list[str] = field(default_factory=list)


def chunk_id(index: int) -> str:
    if index < 1 or index > MAX_SUPPORTED_CHUNK_COUNT:
        raise ValueError("CHUNK_COUNT_OVERFLOW")
    return f"chk_{index:0{CHUNK_COUNTER_WIDTH}d}"
