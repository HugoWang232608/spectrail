from pathlib import Path

from spectrail.cli import main
from spectrail.core.io import read_json


def test_evaluate_cli_generates_passing_report(tmp_path: Path):
    output = tmp_path / "evaluation"
    assert main(["evaluate", "eval/cases/sample_srs/case.json", "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")
    assert report["passed"] is True
    assert report["cases"][0]["requirement_exact_recall"] == 1.0


def test_selected_scope_evaluation_is_reported_explicitly(tmp_path: Path):
    gold = tmp_path / "gold.json"
    gold.write_text(
        '{"items":['
        '{"gold_id":"G1","statement":"系统应允许管理员创建、停用和恢复普通用户账号。",'
        '"sources":[{"block_id":"blk_0006","quote":"管理员应能够创建、停用和恢复普通用户账号。"}]},'
        '{"gold_id":"G2","statement":"当用户账号状态发生变更时，系统应记录操作者、时间和原因。",'
        '"sources":[{"block_id":"blk_0006","quote":"系统应记录用户账号状态变更的操作者、时间和原因。"}]}'
        "]}",
        encoding="utf-8",
    )
    case = tmp_path / "case.json"
    case.write_text(
        "{"
        '"name":"selected-scope","document":"docs/sample_srs.md",'
        f'"gold":"{gold.as_posix()}","scope_block_ids":["blk_0006"],'
        '"thresholds":{"requirement_exact_recall_min":1.0}'
        "}",
        encoding="utf-8",
    )
    output = tmp_path / "selected-evaluation"
    assert main(["evaluate", str(case), "--output", str(output)]) == 0
    report = read_json(output / "evaluation_report.json")["cases"][0]
    assert report["annotation_scope"] == "selected_blocks"
    assert report["scope_block_ids"] == ["blk_0006"]


def test_evaluate_cli_returns_one_when_threshold_fails(tmp_path: Path):
    case = tmp_path / "case.json"
    case.write_text(
        '{"name":"failing-gate","document":"docs/sample_srs.md",'
        '"gold":"eval/cases/sample_srs/gold.json",'
        '"thresholds":{"requirement_exact_recall_min":1.1}}',
        encoding="utf-8",
    )
    assert main(["evaluate", str(case), "--output", str(tmp_path / "report")]) == 1
