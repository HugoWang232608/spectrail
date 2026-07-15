from __future__ import annotations

from spectrail.core.models import DocumentBlock


def render_blocks_to_markdown(blocks: list[DocumentBlock]) -> str:
    rendered: list[str] = []
    for block in blocks:
        if not block.text.strip():
            continue

        if block.type == "table":
            rendered.append(block.text)
            continue

        text = block.text.strip()
        if block.type == "heading":
            level = int(block.metadata.get("level", len(block.section_path) or 1))
            level = min(max(level, 1), 6)
            rendered.append(f"{'#' * level} {text}")
        elif block.type == "code":
            rendered.append(_render_code(text))
        else:
            rendered.append(text)

    return "\n\n".join(rendered) + ("\n" if rendered else "")


def _render_code(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped
    return f"```\n{stripped}\n```"
