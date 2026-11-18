"""確認問題の範囲チェックとMCP越しのモデル入力を検証する。"""

import asyncio
import json

from src.quiz_validation import model_cases, protocol_cases


def test_quiz_protocol_distinguishes_missing_lecture_and_type_error() -> None:
    """未収録回は案内、型不正はMCPのエラー応答になる。"""
    records = {record["case"]: record for record in asyncio.run(protocol_cases(15))}
    valid = records["valid"]
    assert valid["is_error"] is False
    quiz = json.loads(valid["content"][0]["text"])
    assert quiz["lecture"] == 2 and quiz["term"] == "事前学習"
    assert "次のトークン" in quiz["answer"]
    for name in ("missing", "zero", "negative"):
        record = records[name]
        assert record["is_error"] is False
        assert "用語がありません" in record["content"][0]["text"]
        assert "1, 2, 3, 5" in record["content"][0]["text"]
    for name in ("invalid_type", "boolean"):
        assert records[name]["is_error"] is True
        assert "integer" in records[name]["content"][0]["text"]


def test_testmodel_receives_quiz_and_missing_lecture_message() -> None:
    """TestModelへの正常返却と、型エラー時の再試行要求を確認する。"""
    records = {record["case"]: record for record in asyncio.run(model_cases(15))}
    valid = records["lecture_2"]
    assert valid["model"] == "TestModel"
    assert valid["trace"][0]["args"] == {"lecture": 2}
    assert valid["trace"][1]["kind"] == "return"
    assert "事前学習" in valid["trace"][1]["content"]
    missing = records["lecture_99"]
    assert missing["model"] == "TestModel"
    assert missing["trace"][1]["kind"] == "return"
    assert "第99回の用語がありません" in missing["trace"][1]["content"]
    error = records["invalid_type"]
    retries = [event for event in error["trace"] if event["kind"] == "retry"]
    assert len(retries) == 1
    assert retries[0]["name"] == "glossary_make_quiz"
    assert "Error executing tool make_quiz" in str(retries[0]["content"])
    assert all(record["real_llm_used"] is False for record in records.values())
