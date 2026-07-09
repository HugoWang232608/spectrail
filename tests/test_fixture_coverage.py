from spectrail.core.io import read_json
from spectrail.extractors.reqir_extractor import ReqIRExtractor
from spectrail.llm.mock_model import MockModel
from spectrail.parsers.markdown_parser import MarkdownParser
from spectrail.validators.source_quote_validator import SourceQuoteValidator


def test_mock_fixture_covers_markdown_block_types():
    blocks = MarkdownParser().parse_file("docs/sample_srs.md")
    payload = MockModel().generate("")
    requirements = ReqIRExtractor().extract(
        payload=payload,
        blocks=blocks,
        document_name="sample_srs.md",
        model_mode="mock",
    )

    assert len(requirements) >= 14

    blocks_by_id = {block.block_id: block for block in blocks}
    covered_types = {
        blocks_by_id[requirement.sources[0].block_id].type
        for requirement in requirements
        if requirement.sources
    }
    assert {"paragraph", "list", "table", "blockquote", "code"}.issubset(covered_types)

    validated, report = SourceQuoteValidator().validate(requirements, blocks)
    assert report.valid
    assert len(validated) == len(requirements)
    for requirement in validated:
        assert any(
            source.match_status in {"PASS_EXACT", "PASS_NORMALIZED"}
            for source in requirement.sources
        )


def test_fixture_items_are_parseable_requirement_candidates():
    payload = read_json("fixtures/mock_reqir_response.json")
    assert len(payload["items"]) >= 14
