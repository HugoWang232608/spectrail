import json
from pathlib import Path


MANIFEST = Path("eval/real_world_agent_v1/manifest.json")


def test_real_world_agent_corpus_is_diverse_and_reproducible():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents = payload["documents"]

    assert payload["schema_version"] == "real_world_agent_corpus_v1"
    assert 10 <= len(documents) <= 20
    assert len({item["case_id"] for item in documents}) == len(documents)
    assert {item["format"] for item in documents} == {
        "markdown",
        "docx",
        "pdf",
    }
    assert {item["length_class"] for item in documents} >= {
        "short",
        "long",
        "very_long",
    }
    assert any("table" in item["structure_class"] for item in documents)
    assert any(
        item["structure_class"] == "poorly_filled_template"
        for item in documents
    )
    for item in documents:
        assert item["source_url"].startswith("https://")
        assert item["source_page"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)


def test_real_world_documents_are_not_committed():
    tracked_root = Path("eval/real_world_agent_v1")

    assert {path.name for path in tracked_root.iterdir()} == {"manifest.json"}


def test_real_world_agent_evaluator_records_required_observations():
    source = Path("scripts/evaluate_real_world_agent.py").read_text(
        encoding="utf-8"
    )

    for field in (
        "success",
        "failure_reason",
        "quality_proxy_score",
        "fully_grounded_requirement_rate",
        "retry_count",
        "planner_tool_sequence",
        "total_tokens",
        "wall_elapsed_ms",
    ):
        assert field in source
