"""学習支援エージェントの仕様を検証する。"""

import json
import pytest
from src import tools


@pytest.mark.parametrize("lecture", [1, 2, 3, 5])
def test_quiz_is_grounded_in_requested_lecture(lecture: int) -> None:
    """指定回の用語の定義が、問題の正解と一致する。"""
    quiz = json.loads(tools.make_quiz(lecture))
    entry = next(term for term in tools.load_glossary() if term["term"] == quiz["term"])
    assert quiz["lecture"] == entry["lecture"] == lecture
    assert quiz["answer"] == entry["definition"]
    assert quiz["term"] in quiz["question"]


@pytest.mark.parametrize("lecture", [-1, 0, 4, 999])
def test_quiz_does_not_invent_missing_lecture(lecture: int) -> None:
    """未収録の回では問題を創作せず、案内を返す。"""
    assert "用語がありません" in tools.make_quiz(lecture)
