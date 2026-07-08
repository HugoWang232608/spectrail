from __future__ import annotations

from spectrail.core.models import RequirementIR


def build_source_map(requirements: list[RequirementIR]) -> dict:
    entries = []
    for requirement in requirements:
        for source in requirement.sources:
            entries.append(
                {
                    "requirement_id": requirement.id,
                    "source": source.model_dump(mode="json"),
                }
            )
    return {"items": entries}
