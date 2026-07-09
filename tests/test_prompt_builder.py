from spectrail.core.models import DocumentBlock
from spectrail.llm.base import ModelRequest
from spectrail.llm.prompt_builder import build_reqir_prompt


def test_reqir_prompt_requires_numeric_confidence():
    prompt = build_reqir_prompt(
        ModelRequest(
            document_text="The system shall log failed sign-in attempts.",
            blocks=[
                DocumentBlock(
                    block_id="blk_0001",
                    document_id="doc_1",
                    type="paragraph",
                    text="The system shall log failed sign-in attempts.",
                    order=1,
                )
            ],
            document_name="sample.md",
            source_format="markdown",
            parser_name="markdown_parser_v1",
            model_mode="live",
        )
    )

    assert "confidence must be a number from 0.0 to 1.0" in prompt
    assert "not textual labels such as high/medium/low" in prompt
