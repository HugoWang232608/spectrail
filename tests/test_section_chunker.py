from spectrail.chunking import ChunkingConfig, SectionAwareChunker
from spectrail.chunking.models import chunk_id
from spectrail.core.models import DocumentBlock
from spectrail.llm.base import ModelRequest
from spectrail.llm.prompt_builder import build_reqir_prompt


def _blocks(count: int = 20) -> list[DocumentBlock]:
    return [
        DocumentBlock(
            block_id=f"blk_{index:04d}",
            document_id="doc_001",
            type="paragraph",
            text=f"Requirement {index}: The system shall retain deterministic evidence. " * 2,
            section_path=[f"Section {(index - 1) // 4}"],
            order=index,
        )
        for index in range(1, count + 1)
    ]


def _request_factory(blocks, metadata):
    return ModelRequest(
        document_text="\n".join(block.text for block in blocks),
        blocks=blocks,
        document_name="sample.md",
        source_format="markdown",
        parser_name="markdown_parser_v1",
        model_mode="mock",
        metadata=metadata,
    )


def test_forced_chunking_respects_final_prompt_budget_and_progress():
    blocks = _blocks()
    config = ChunkingConfig(mode="force", max_rendered_prompt_chars=1600, overlap_blocks=1)
    chunks = SectionAwareChunker().chunk(
        blocks,
        config,
        request_factory=_request_factory,
        prompt_renderer=build_reqir_prompt,
    )
    assert len(chunks) >= 3
    assert all(chunk.chunk_id.startswith("chk_") and len(chunk.chunk_id) == 12 for chunk in chunks)
    assert all(chunk.new_block_ids for chunk in chunks)
    assert all(chunk.rendered_prompt_chars <= config.max_rendered_prompt_chars for chunk in chunks)
    assert [block_id for chunk in chunks for block_id in chunk.new_block_ids] == [
        block.block_id for block in blocks
    ]


def test_empty_blocks_fail_before_model_planning():
    try:
        SectionAwareChunker().chunk(
            [],
            ChunkingConfig(),
            request_factory=_request_factory,
            prompt_renderer=build_reqir_prompt,
        )
    except ValueError as exc:
        assert str(exc) == "NO_EXTRACTABLE_CONTENT"
    else:
        raise AssertionError("expected empty input failure")


def test_chunk_ids_keep_fixed_width_across_decimal_boundaries():
    assert [chunk_id(value) for value in [9, 10, 99, 100, 9999, 10000]] == [
        "chk_00000009",
        "chk_00000010",
        "chk_00000099",
        "chk_00000100",
        "chk_00009999",
        "chk_00010000",
    ]


def test_chunking_off_keeps_single_over_budget_chunk_with_warning():
    blocks = _blocks()
    chunks = SectionAwareChunker().chunk(
        blocks,
        ChunkingConfig(mode="off", max_rendered_prompt_chars=1000),
        request_factory=_request_factory,
        prompt_renderer=build_reqir_prompt,
    )
    assert len(chunks) == 1
    assert chunks[0].warnings == ["CHUNK_PROMPT_OVER_BUDGET"]
