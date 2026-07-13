from spectrail.aggregation import CandidateAggregator
from spectrail.core.models import DocumentBlock, RequirementIR, SourceSpan


def _candidate(identifier: str, chunk: str, requirement_type: str, block_id: str = "blk_0001"):
    return RequirementIR(
        id=identifier,
        statement="The system shall start.",
        type=requirement_type,
        sources=[
            SourceSpan(
                document_id="doc_001",
                block_id=block_id,
                quote="The system shall start.",
            )
        ],
        metadata={"chunk_id": chunk, "chunk_index": int(chunk[-1]), "local_item_index": 0},
    )


def test_aggregator_collapses_exact_candidates_and_preserves_conflicts():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall start.",
            order=1,
        )
    ]
    result = CandidateAggregator().aggregate(
        [
            _candidate("C1", "chk_00000001", "functional"),
            _candidate("C2", "chk_00000002", "constraint"),
        ],
        blocks,
    )
    assert len(result.requirements) == 1
    assert result.collapsed_exact_candidates == 1
    assert result.field_conflict_count == 1
    assert result.requirements[0].review_status == "needs_recheck"
    assert len(result.requirements[0].metadata["aggregation_variants"]) == 2


def test_aggregator_sorts_unknown_source_after_known_source():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="paragraph",
            text="The system shall start.",
            order=1,
        )
    ]
    known = _candidate("C1", "chk_00000001", "functional")
    unknown = _candidate("C2", "chk_00000002", "functional", block_id="missing")
    unknown.statement = "The system shall stop."
    result = CandidateAggregator().aggregate([unknown, known], blocks)
    assert [item.sources[0].block_id for item in result.requirements] == ["blk_0001", "missing"]
    assert [item.id for item in result.requirements] == ["REQ-0001", "REQ-0002"]


def test_aggregator_preserves_same_quote_with_different_canonical_cells():
    blocks = [
        DocumentBlock(
            block_id="blk_0001",
            document_id="doc_001",
            type="table",
            text="value",
            order=1,
        )
    ]
    first = _candidate("C1", "chk_00000001", "functional")
    second = _candidate("C2", "chk_00000002", "functional")
    first.sources[0].canonical_source_cell_ids = ["cell_a"]
    second.sources[0].canonical_source_cell_ids = ["cell_b"]

    result = CandidateAggregator().aggregate([first, second], blocks)

    assert len(result.requirements) == 2
    assert result.collapsed_exact_candidates == 0
