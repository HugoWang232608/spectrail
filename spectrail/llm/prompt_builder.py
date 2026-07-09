from __future__ import annotations

from spectrail.llm.base import ModelRequest


PROMPT_VERSION = "reqir_extraction_v1"


def build_reqir_prompt(request: ModelRequest, *, max_blocks: int | None = None) -> str:
    blocks = request.blocks[:max_blocks] if max_blocks is not None else request.blocks
    rendered_blocks = "\n\n".join(_render_block(block) for block in blocks)
    return (
        "You are extracting software requirements into ReqIR JSON.\n\n"
        "Return JSON only with a top-level items array.\n\n"
        "Each item must include title, type, ears_pattern, statement, subject, response, "
        "source_block_id, source_quote, confidence, and tags.\n\n"
        "Rules:\n"
        "- source_block_id must be one of the provided block IDs.\n"
        "- source_quote must be an exact substring from the chosen block text.\n"
        "- Do not invent requirements not supported by source text.\n\n"
        f"Document: {request.document_name}\n"
        f"Source format: {request.source_format}\n"
        f"Parser: {request.parser_name}\n\n"
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
