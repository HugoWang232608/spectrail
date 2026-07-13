from __future__ import annotations

from math import ceil
from typing import Callable

from spectrail.chunking.models import (
    CHUNK_COUNTER_WIDTH,
    MAX_SUPPORTED_CHUNK_COUNT,
    ChunkingConfig,
    DocumentChunk,
    chunk_id,
)
from spectrail.core.models import DocumentBlock
from spectrail.llm.base import ModelRequest
from spectrail.llm.fingerprints import sha256_hex


class ChunkPlanningError(ValueError):
    pass


CHUNKER_VERSION = "section_aware_v2"


RequestFactory = Callable[[list[DocumentBlock], dict], ModelRequest]
PromptRenderer = Callable[[ModelRequest], str]


class SectionAwareChunker:
    def chunk(
        self,
        blocks: list[DocumentBlock],
        config: ChunkingConfig,
        *,
        request_factory: RequestFactory,
        prompt_renderer: PromptRenderer,
    ) -> list[DocumentChunk]:
        if not blocks:
            raise ChunkPlanningError("NO_EXTRACTABLE_CONTENT")
        ordered = sorted(blocks, key=lambda block: block.order)
        if len({block.block_id for block in ordered}) != len(ordered):
            raise ChunkPlanningError("duplicate block_id")
        if len({block.order for block in ordered}) != len(ordered):
            raise ChunkPlanningError("duplicate block order")

        single_prompt = prompt_renderer(request_factory(ordered, {"chunked": False}))
        if config.mode == "off" or (
            config.mode == "auto"
            and len(single_prompt) <= config.max_rendered_prompt_chars
            and len(ordered) < config.min_blocks_for_auto
        ):
            warnings = [] if len(single_prompt) <= config.max_rendered_prompt_chars else ["CHUNK_PROMPT_OVER_BUDGET"]
            return [self._make_chunk(1, ordered, ordered, [], [], single_prompt, config, warnings)]

        count_rendered = "0" * CHUNK_COUNTER_WIDTH
        provisional: list[DocumentChunk] | None = None
        for _planning_pass in range(1, 4):
            provisional = self._plan(
                ordered,
                config,
                count_rendered=count_rendered,
                request_factory=request_factory,
                prompt_renderer=prompt_renderer,
            )
            if len(provisional) > MAX_SUPPORTED_CHUNK_COUNT:
                raise ChunkPlanningError("CHUNK_COUNT_OVERFLOW")
            final_count = f"{len(provisional):0{CHUNK_COUNTER_WIDTH}d}"
            final_chunks = self._rerender(
                provisional,
                final_count,
                config,
                request_factory=request_factory,
                prompt_renderer=prompt_renderer,
            )
            if all(
                "CHUNK_OVERSIZED_BLOCK" in chunk.warnings
                or chunk.rendered_prompt_chars <= config.max_rendered_prompt_chars
                for chunk in final_chunks
            ):
                return final_chunks
            count_rendered = final_count
        raise ChunkPlanningError("CHUNK_PLANNING_DID_NOT_CONVERGE")

    def _plan(
        self,
        blocks: list[DocumentBlock],
        config: ChunkingConfig,
        *,
        count_rendered: str,
        request_factory: RequestFactory,
        prompt_renderer: PromptRenderer,
    ) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        position = 0
        previous_new: list[DocumentBlock] = []
        while position < len(blocks):
            index = len(chunks) + 1
            context = self._heading_context(blocks, position)
            overlap = [block for block in previous_new if block.type != "heading"][-config.overlap_blocks :]
            new_blocks: list[DocumentBlock] = []
            while position < len(blocks):
                group_end = position + 1
                section_path = blocks[position].section_path
                while group_end < len(blocks) and blocks[group_end].section_path == section_path:
                    group_end += 1
                group = blocks[position:group_end]
                candidate_new = [*new_blocks, *group]
                prompt = self._render(
                    [*context, *overlap, *candidate_new],
                    index,
                    count_rendered,
                    new_block_ids=[block.block_id for block in candidate_new],
                    overlap_block_ids=[block.block_id for block in overlap],
                    context_block_ids=[block.block_id for block in context],
                    request_factory=request_factory,
                    prompt_renderer=prompt_renderer,
                )
                if len(prompt) <= config.max_rendered_prompt_chars:
                    new_blocks = candidate_new
                    position = group_end
                    continue
                if new_blocks:
                    break
                # The section group is too large. Consume as many individual blocks as fit.
                for block in group:
                    candidate_new = [*new_blocks, block]
                    while overlap:
                        prompt = self._render(
                            [*context, *overlap, *candidate_new],
                            index,
                            count_rendered,
                            new_block_ids=[item.block_id for item in candidate_new],
                            overlap_block_ids=[item.block_id for item in overlap],
                            context_block_ids=[item.block_id for item in context],
                            request_factory=request_factory,
                            prompt_renderer=prompt_renderer,
                        )
                        if len(prompt) <= config.max_rendered_prompt_chars:
                            break
                        overlap = overlap[1:]
                    while context:
                        prompt = self._render(
                            [*context, *candidate_new],
                            index,
                            count_rendered,
                            new_block_ids=[item.block_id for item in candidate_new],
                            overlap_block_ids=[],
                            context_block_ids=[item.block_id for item in context],
                            request_factory=request_factory,
                            prompt_renderer=prompt_renderer,
                        )
                        if len(prompt) <= config.max_rendered_prompt_chars:
                            break
                        context = context[1:]
                    prompt = self._render(
                        [*context, *overlap, *candidate_new],
                        index,
                        count_rendered,
                        new_block_ids=[item.block_id for item in candidate_new],
                        overlap_block_ids=[item.block_id for item in overlap],
                        context_block_ids=[item.block_id for item in context],
                        request_factory=request_factory,
                        prompt_renderer=prompt_renderer,
                    )
                    if len(prompt) > config.max_rendered_prompt_chars and new_blocks:
                        break
                    new_blocks = candidate_new
                    position += 1
                    if len(prompt) > config.max_rendered_prompt_chars:
                        break
                break
            if not new_blocks:
                raise ChunkPlanningError("CHUNK_PLANNING_STALLED")
            prompt = self._render(
                [*context, *overlap, *new_blocks],
                index,
                count_rendered,
                new_block_ids=[block.block_id for block in new_blocks],
                overlap_block_ids=[block.block_id for block in overlap],
                context_block_ids=[block.block_id for block in context],
                request_factory=request_factory,
                prompt_renderer=prompt_renderer,
            )
            warnings = ["CHUNK_OVERSIZED_BLOCK"] if len(prompt) > config.max_rendered_prompt_chars else []
            chunks.append(
                self._make_chunk(
                    index,
                    [*context, *overlap, *new_blocks],
                    new_blocks,
                    overlap,
                    context,
                    prompt,
                    config,
                    warnings,
                )
            )
            previous_new = new_blocks
        return chunks

    def _rerender(
        self,
        chunks: list[DocumentChunk],
        count_rendered: str,
        config: ChunkingConfig,
        *,
        request_factory: RequestFactory,
        prompt_renderer: PromptRenderer,
    ) -> list[DocumentChunk]:
        result = []
        for chunk in chunks:
            prompt = self._render(
                chunk.blocks,
                chunk.index,
                count_rendered,
                new_block_ids=chunk.new_block_ids,
                overlap_block_ids=chunk.overlap_block_ids,
                context_block_ids=chunk.context_block_ids,
                request_factory=request_factory,
                prompt_renderer=prompt_renderer,
            )
            result.append(
                self._make_chunk(
                    chunk.index,
                    chunk.blocks,
                    [block for block in chunk.blocks if block.block_id in chunk.new_block_ids],
                    [block for block in chunk.blocks if block.block_id in chunk.overlap_block_ids],
                    [block for block in chunk.blocks if block.block_id in chunk.context_block_ids],
                    prompt,
                    config,
                    chunk.warnings,
                )
            )
        return result

    @staticmethod
    def _render(
        blocks: list[DocumentBlock],
        index: int,
        count_rendered: str,
        *,
        new_block_ids: list[str],
        overlap_block_ids: list[str],
        context_block_ids: list[str],
        request_factory: RequestFactory,
        prompt_renderer: PromptRenderer,
    ) -> str:
        metadata = {
            "chunk_id": chunk_id(index),
            "chunk_index": index,
            "chunk_index_rendered": f"{index:0{CHUNK_COUNTER_WIDTH}d}",
            "chunk_count_rendered": count_rendered,
            "chunked": True,
            "new_block_ids": new_block_ids,
            "overlap_block_ids": overlap_block_ids,
            "context_block_ids": context_block_ids,
        }
        return prompt_renderer(request_factory(blocks, metadata))

    @staticmethod
    def _make_chunk(
        index: int,
        blocks: list[DocumentBlock],
        new_blocks: list[DocumentBlock],
        overlap: list[DocumentBlock],
        context: list[DocumentBlock],
        prompt: str,
        config: ChunkingConfig,
        warnings: list[str],
    ) -> DocumentChunk:
        fingerprint = sha256_hex(
            {
                "block_ids": [block.block_id for block in blocks],
                "new_block_ids": [block.block_id for block in new_blocks],
                "overlap_block_ids": [block.block_id for block in overlap],
                "context_block_ids": [block.block_id for block in context],
                "normalized_block_texts": [" ".join(block.text.split()) for block in blocks],
                "section_path": new_blocks[0].section_path if new_blocks else [],
                "chunk_identity_config": {
                    "chunker_version": CHUNKER_VERSION,
                    "chunk_counter_width": CHUNK_COUNTER_WIDTH,
                    "mode": config.mode,
                    "max_rendered_prompt_chars": config.max_rendered_prompt_chars,
                    "overlap_blocks": config.overlap_blocks,
                    "min_blocks_for_auto": config.min_blocks_for_auto,
                },
            }
        )
        content_chars = sum(len(block.text) for block in blocks)
        return DocumentChunk(
            chunk_id=chunk_id(index),
            index=index,
            blocks=blocks,
            block_ids=[block.block_id for block in blocks],
            new_block_ids=[block.block_id for block in new_blocks],
            overlap_block_ids=[block.block_id for block in overlap],
            context_block_ids=[block.block_id for block in context],
            section_path=list(new_blocks[0].section_path if new_blocks else []),
            content_chars=content_chars,
            rendered_prompt_chars=len(prompt),
            estimated_tokens=max(1, ceil(len(prompt) / 4)),
            chunk_fingerprint=fingerprint,
            warnings=list(warnings),
        )

    @staticmethod
    def _heading_context(blocks: list[DocumentBlock], position: int) -> list[DocumentBlock]:
        if position <= 0 or blocks[position].type == "heading":
            return []
        target_path = blocks[position].section_path
        if not target_path:
            return []
        by_depth: dict[int, DocumentBlock] = {}
        for block in blocks[:position]:
            if block.type != "heading" or not block.section_path:
                continue
            depth = len(block.section_path)
            if block.section_path == target_path[:depth]:
                by_depth[depth] = block
        return [by_depth[depth] for depth in sorted(by_depth)]
