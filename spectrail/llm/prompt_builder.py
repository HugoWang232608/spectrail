from __future__ import annotations

from spectrail.llm.base import ModelRequest


PROMPT_VERSION = "reqir_extraction_v1"
CHUNKED_PROMPT_VERSION = "reqir_extraction_v2_chunked"


def build_reqir_prompt(request: ModelRequest, *, max_blocks: int | None = None) -> str:
    blocks = request.blocks[:max_blocks] if max_blocks is not None else request.blocks
    rendered_blocks = "\n\n".join(_render_block(block) for block in blocks)
    chunk_context = ""
    if request.metadata.get("chunked") or request.metadata.get("chunk_id"):
        chunk_context = (
            "Chunk context:\n"
            f"- chunk_id: {request.metadata.get('chunk_id', '')}\n"
            f"- chunk_index: {request.metadata.get('chunk_index_rendered', request.metadata.get('chunk_index', ''))}\n"
            f"- chunk_count: {request.metadata.get('chunk_count_rendered', request.metadata.get('chunk_count', ''))}\n"
            "- Extract requirements only from the blocks in this chunk.\n"
            "- Never cite a block ID that is absent from this chunk.\n\n"
        )
    return (
        "You are extracting software requirements into ReqIR JSON.\n\n"
        "Return JSON only with a top-level items array.\n\n"
        "Each item must include title, type, ears_pattern, statement, subject, response, "
        "source_block_id, source_quote, confidence, and tags.\n\n"
        "Allowed enum values:\n"
        "- type: functional | non_functional | interface | constraint | business | unknown\n"
        "- ears_pattern: ubiquitous | event_driven | state_driven | optional | unwanted_behavior | unknown\n"
        "- priority: high | medium | low | unknown\n"
        "- verification_method: test | inspection | analysis | demonstration | unknown\n\n"
        "Rules:\n"
        "- source_block_id must be one of the provided block IDs.\n"
        "- source_quote must be an exact substring from the chosen block text.\n"
        "- confidence must be a number from 0.0 to 1.0, not textual labels such as high/medium/low.\n"
        "- Use unknown for unsupported enum values instead of inventing new enum labels.\n"
        "- Do not invent requirements not supported by source text.\n\n"
        f"Document: {request.document_name}\n"
        f"Source format: {request.source_format}\n"
        f"Parser: {request.parser_name}\n\n"
        f"{chunk_context}"
        "Blocks:\n\n"
        f"{rendered_blocks}"
    )


def _render_block(block) -> str:
    section_path = " > ".join(block.section_path) if block.section_path else ""
    return (
        f"[{block.block_id}]\n"
        f"type: {block.type}\n"
        f"section_path: {section_path}\n"
        f"text: {block.text}"
    )
