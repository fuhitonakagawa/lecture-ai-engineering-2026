"""処理と引数を固定し、講義要約ツールの説明だけを選択する。"""

from collections.abc import Callable

from src import tools


def brief_summary(lecture: int) -> str:
    """指定した回の資料を返す。"""
    return tools.get_lecture_summary(lecture)


def detailed_summary(lecture: int) -> str:
    """指定した講義回で何を学ぶかを確認するために、要点のMarkdownを返す。

    「第2回では何を学びますか」「第3回の内容を教えて」のように、
    回全体のテーマや学習内容を尋ねられたときに使う。
    個別の用語の定義はsearch_glossary、確認問題の出題はmake_quizを使う。

    Args:
        lecture: 調べる講義回。1始まりの整数。

    Returns:
        講義の要点。未収録の回では利用可能な回の案内。
    """
    return tools.get_lecture_summary(lecture)


def tool_functions(style: str) -> dict[str, Callable[..., str]]:
    """公開する名前と、対応するdocstringを持つ関数を返す。"""
    if style not in {"brief", "detailed"}:
        raise ValueError(
            "MCP_DESCRIPTION_STYLEはbriefまたはdetailedを指定してください。"
        )
    summary = brief_summary if style == "brief" else detailed_summary
    summary.__name__ = "get_lecture_summary"
    return {
        "search_glossary": tools.search_glossary,
        "get_lecture_summary": summary,
        "make_quiz": tools.make_quiz,
    }
