from spectrail.core.models import RequirementIR, SourceSpan
from spectrail.evaluation.locator_metrics import bbox_iou, build_locator_metrics
from spectrail.evaluation.matcher import match_requirements
from spectrail.evaluation.models import GoldRequirement, GoldSource
from spectrail.evidence import (
    BlockEvidenceRecord,
    BoundingBox,
    CapabilityValidationResult,
    PageLocator,
    TableLocator,
    TextLocator,
    sha256_text,
)


def test_match_pair_records_the_actual_second_gold_source():
    candidate = RequirementIR(
        id="C1",
        statement="Statement",
        sources=[SourceSpan(document_id="doc_001", block_id="blk_0001", quote="second")],
    )
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="Statement",
            sources=[
                GoldSource(block_id="blk_0001", quote="first"),
                GoldSource(block_id="blk_0001", quote="second"),
            ],
        )
    ]
    pair = match_requirements([candidate], gold).source_alignment_matches[0]
    assert pair.candidate_source_index == 0
    assert pair.gold_source_index == 1


def test_locator_metrics_use_source_alignment_pair_and_expected_capabilities():
    bbox = BoundingBox(x0=10, y0=20, x1=50, y1=60)
    source = SourceSpan(
        document_id="doc_001",
        block_id="blk_0001",
        quote="source quote",
        match_status="PASS_EXACT",
        text_locator=TextLocator(
            block_id="blk_0001", start=0, end=12, match_basis="exact"
        ),
        page_locator=PageLocator(
            page=2,
            bbox=bbox,
            page_width=100,
            page_height=100,
            derivation="quote_span_union",
        ),
        table_locator=TableLocator(
            table_id="tbl_00000001",
            cell_ids=["c1", "c2"],
            row_indices=[1, 1],
            selected_row_index=1,
            column_indices=[1, 2],
            bbox=bbox,
        ),
        capability_results=[
            CapabilityValidationResult(capability="text_range", status="PASS"),
            CapabilityValidationResult(capability="page_region", status="PASS"),
            CapabilityValidationResult(capability="table_cell", status="PASS"),
        ],
    )
    candidates = [RequirementIR(id="C1", statement="Wrong statement", sources=[source])]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="Correct statement",
            sources=[
                GoldSource(
                    block_id="blk_0001",
                    quote="source quote",
                    page=2,
                    table_id="tbl_00000001",
                    cell_ids=["c1", "c2"],
                    bbox=bbox,
                )
            ],
        )
    ]
    matches = match_requirements(candidates, gold)
    assert len(matches.source_alignment_matches) == 1
    assert matches.requirement_exact_matches == []
    metrics = build_locator_metrics(
        candidates=candidates,
        gold=gold,
        matches=matches,
        block_evidence=[
            BlockEvidenceRecord(
                block_id="blk_0001",
                text_length=12,
                text_sha256=sha256_text("source quote"),
                table_id="tbl_00000001",
                table_row_index=1,
                cell_ids=["cell_00000001_r0001_c0001"],
                expected_capabilities=["text_range", "page_region", "table_cell"],
                available_capabilities=["text_range", "page_region", "table_cell"],
            )
        ],
    )
    assert metrics["page_accuracy"] == 1.0
    assert metrics["table_cell_f1"] == 1.0
    assert metrics["bbox_iou_pass_rate"] == 1.0
    assert metrics["text_locator_pass_rate"] == 1.0
    assert metrics["structured_grounding_coverage"] == 1.0


def test_bbox_iou_rejects_non_overlapping_boxes():
    assert bbox_iou(
        BoundingBox(x0=0, y0=0, x1=1, y1=1),
        BoundingBox(x0=2, y0=2, x1=3, y1=3),
    ) == 0.0


def test_bbox_metric_requires_matching_page():
    bbox = BoundingBox(x0=10, y0=20, x1=50, y1=60)
    source = SourceSpan(
        document_id="doc_001",
        block_id="blk_0001",
        quote="quote",
        page_locator=PageLocator(
            page=3,
            bbox=bbox,
            page_width=100,
            page_height=100,
            derivation="quote_span_union",
        ),
    )
    candidates = [RequirementIR(id="C1", statement="S", sources=[source])]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="S",
            sources=[GoldSource(block_id="blk_0001", quote="quote", page=2, bbox=bbox)],
        )
    ]
    metrics = build_locator_metrics(
        candidates=candidates,
        gold=gold,
        matches=match_requirements(candidates, gold),
    )
    assert metrics["page_accuracy"] == 0.0
    assert metrics["bbox_iou_pass_rate"] == 0.0


def test_bbox_metric_prefers_table_bbox_for_cell_gold():
    page_bbox = BoundingBox(x0=0, y0=0, x1=20, y1=20)
    table_bbox = BoundingBox(x0=40, y0=40, x1=80, y1=80)
    source = SourceSpan(
        document_id="doc_001",
        block_id="blk_0001",
        quote="quote",
        page_locator=PageLocator(
            page=2,
            bbox=page_bbox,
            page_width=100,
            page_height=100,
            derivation="quote_span_union",
        ),
        table_locator=TableLocator(
            table_id="table_1",
            cell_ids=["cell_1"],
            row_indices=[1],
            selected_row_index=1,
            column_indices=[1],
            bbox=table_bbox,
        ),
    )
    candidates = [RequirementIR(id="C1", statement="S", sources=[source])]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="S",
            sources=[
                GoldSource(
                    block_id="blk_0001",
                    quote="quote",
                    page=2,
                    table_id="table_1",
                    cell_ids=["cell_1"],
                    bbox=table_bbox,
                )
            ],
        )
    ]
    metrics = build_locator_metrics(
        candidates=candidates,
        gold=gold,
        matches=match_requirements(candidates, gold),
    )
    assert metrics["bbox_iou_pass_rate"] == 1.0


def test_structured_diagnostics_explain_every_expected_capability():
    source = SourceSpan(
        document_id="doc_001",
        block_id="blk_0001",
        quote="quote",
        capability_results=[
            CapabilityValidationResult(
                capability="page_region", status="FAIL_INVALID_REFERENCE"
            ),
            CapabilityValidationResult(capability="table_cell", status="UNVERIFIED"),
        ],
    )
    candidates = [RequirementIR(id="C1", statement="S", sources=[source])]
    gold = [
        GoldRequirement(
            gold_id="G1",
            statement="S",
            sources=[GoldSource(block_id="blk_0001", quote="quote")],
        )
    ]
    metrics = build_locator_metrics(
        candidates=candidates,
        gold=gold,
        matches=match_requirements(candidates, gold),
        block_evidence=[
            BlockEvidenceRecord(
                block_id="blk_0001",
                text_length=5,
                text_sha256=sha256_text("quote"),
                expected_capabilities=["text_range", "page_region", "table_cell"],
            )
        ],
    )
    assert metrics["structured_grounding_failed_count"] == 1
    assert metrics["structured_capability_expected_count"] == 2
    assert metrics["structured_capability_failed_count"] == 1
    assert metrics["structured_capability_unverified_count"] == 1
    assert metrics["structured_invalid_reference_count"] == 1
