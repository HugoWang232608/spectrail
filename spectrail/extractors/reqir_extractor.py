from __future__ import annotations

from pathlib import Path
from typing import Any

from spectrail.core.ids import requirement_id
from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan


class ReqIRExtractor:
    extractor_version = "p0_mock_v1"

    def extract(
        self,
        payload: dict[str, Any],
        blocks: list[DocumentBlock],
        document_name: str,
        model_mode: str = "mock",
    ) -> list[RequirementIR]:
        if not isinstance(payload, dict):
            raise ValueError("model output is not a JSON object")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("model output must contain an items array")

        by_id = {block.block_id: block for block in blocks}
        requirements: list[RequirementIR] = []
        for index, item in enumerate(items, start=1):
            requirements.append(
                self._item_to_requirement(
                    item=item,
                    index=index,
                    by_id=by_id,
                    document_name=Path(document_name).name,
                    model_mode=model_mode,
                )
            )
        return requirements

    def _item_to_requirement(
        self,
        item: Any,
        index: int,
        by_id: dict[str, DocumentBlock],
        document_name: str,
        model_mode: str,
    ) -> RequirementIR:
        if not isinstance(item, dict):
            raise ValueError(f"item {index} is not a JSON object")
        for field in ("statement", "source_block_id", "source_quote"):
            if not item.get(field):
                raise ValueError(f"item {index} missing required field: {field}")
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"item {index} confidence is not numeric") from exc

        source_block_id = str(item["source_block_id"])
        source_block = by_id.get(source_block_id)
        section_path = list(source_block.section_path) if source_block else []
        section = " > ".join(section_path) if section_path else None
        source = SourceSpan(
            document_id=source_block.document_id if source_block else "doc_001",
            document_name=document_name,
            page=source_block.page if source_block else None,
            section=section,
            section_path=section_path,
            block_id=source_block_id,
            quote=str(item["source_quote"]),
        )
        return RequirementIR(
            id=requirement_id(index),
            title=item.get("title"),
            type=item.get("type", "unknown"),
            ears_pattern=item.get("ears_pattern", "unknown"),
            statement=str(item["statement"]),
            subject=item.get("subject"),
            condition=item.get("condition"),
            response=item.get("response"),
            priority=item.get("priority", "unknown"),
            verification_method=item.get("verification_method", "unknown"),
            sources=[source],
            confidence=confidence,
            review_status="pending",
            tags=list(item.get("tags", [])),
            metadata={
                "source_block_id": source_block_id,
                "model_mode": model_mode,
                "extractor_version": self.extractor_version,
                "raw_item_index": index - 1,
            },
        )
