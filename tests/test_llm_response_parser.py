import pytest

from spectrail.llm.errors import ModelResponseParseError
from spectrail.llm.response_parser import parse_model_response


def test_parse_model_response_accepts_plain_json():
    assert parse_model_response('{"items": []}') == {"items": []}


def test_parse_model_response_accepts_fenced_json():
    assert parse_model_response('```json\n{"items": []}\n```') == {"items": []}


def test_parse_model_response_accepts_surrounding_text():
    assert parse_model_response('Here is the result:\n{"items": []}\nDone.') == {"items": []}


def test_parse_model_response_rejects_invalid_text():
    with pytest.raises(ModelResponseParseError):
        parse_model_response("no json here")


def test_parse_model_response_leaves_schema_validation_to_extractor():
    assert parse_model_response('{"not_items": []}') == {"not_items": []}
