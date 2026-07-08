from pathlib import Path

from spectrail.parsers.markdown_parser import MarkdownParser


def test_markdown_parser_stable_blocks_and_sections():
    blocks = MarkdownParser().parse_file(Path("docs/sample_srs.md"))

    assert blocks[0].block_id == "blk_0001"
    assert blocks[0].type == "heading"
    assert blocks[0].section_path == ["智能门禁系统需求规格说明书"]

    by_id = {block.block_id: block for block in blocks}
    assert by_id["blk_0006"].type == "list"
    assert "用户与权限" in " > ".join(by_id["blk_0006"].section_path)
    assert by_id["blk_0018"].type == "table"
    assert by_id["blk_0021"].type == "blockquote"
    assert by_id["blk_0022"].type == "code"


def test_markdown_parser_splits_common_block_types():
    text = """# Title

Paragraph text.

- a
- b

| A | B |
| --- | --- |
| 1 | 2 |

> quote

```text
code
```
"""
    blocks = MarkdownParser().parse_text(text)
    assert [block.type for block in blocks] == [
        "heading",
        "paragraph",
        "list",
        "table",
        "blockquote",
        "code",
    ]
