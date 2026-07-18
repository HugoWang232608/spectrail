from __future__ import annotations

from pathlib import Path

from spectrail.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_generation_bound_api_parameters_remain_required_in_openapi() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    for path in (
        "/api/tasks/{task_id}/reqir",
        "/api/tasks/{task_id}/chunks",
        "/api/tasks/{task_id}/quarantined",
        "/api/tasks/{task_id}/exports/reqir.json",
        "/api/tasks/{task_id}/exports/requirements.xlsx",
    ):
        assert _required_query_parameters(paths[path]["get"]) >= {
            "expected_run_generation"
        }

    for path in (
        "/api/tasks/{task_id}/blocks",
        "/api/tasks/{task_id}/pages/{page_number}/preview.png",
        "/api/tasks/{task_id}/tables/{table_id}/blocks/{block_id}/evidence",
    ):
        assert _required_query_parameters(paths[path]["get"]) >= {
            "expected_evidence_fingerprint",
            "expected_run_generation",
        }

    review_schema = schema["components"]["schemas"]["ReviewRequest"]
    assert set(review_schema["required"]) >= {
        "requirement_id",
        "expected_run_generation",
        "expected_review_revision",
        "action",
    }


def test_public_api_examples_include_generation_and_revision_guards() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    api_doc = (ROOT / "docs" / "p1_api.md").read_text(encoding="utf-8")
    p2_doc = (ROOT / "docs" / "p2_docx_pdf_best_effort.md").read_text(
        encoding="utf-8"
    )

    for document in (readme, api_doc):
        _assert_route_window_contains(
            document,
            "/api/tasks/{task_id}/review",
            '"expected_run_generation":1',
            '"expected_review_revision":0',
        )
        _assert_route_window_contains(
            document,
            "/api/tasks/{task_id}/exports/requirements.xlsx",
            "expected_run_generation=1",
        )

    _assert_route_window_contains(
        readme,
        "/api/tasks/{task_id}/pages/{page_number}/preview.png",
        "expected_evidence_fingerprint",
        "expected_run_generation",
    )
    _assert_route_window_contains(
        readme,
        "/api/tasks/{task_id}/blocks",
        "expected_evidence_fingerprint",
        "expected_run_generation",
    )
    _assert_route_window_contains(
        readme,
        "/api/tasks/{task_id}/tables/{table_id}/blocks/{block_id}/evidence",
        "expected_evidence_fingerprint",
        "expected_run_generation",
    )

    assert "Historical scope note" in p2_doc
    assert "[p5_evidence_review.md](p5_evidence_review.md)" in p2_doc


def _required_query_parameters(operation: dict) -> set[str]:
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query" and parameter.get("required") is True
    }


def _assert_route_window_contains(
    document: str,
    route: str,
    *required_fragments: str,
) -> None:
    route_index = document.index(route)
    window = document[route_index : route_index + 700]
    for fragment in required_fragments:
        assert fragment in window, f"{route} example is missing {fragment}"
