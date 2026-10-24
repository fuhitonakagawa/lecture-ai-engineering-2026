"""モデル比較の入力条件、保存内容、失敗時の挙動を検証する。"""

import json
import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from app import codex_comparison as comparison

ENV_SETTINGS = {
    "CODEX_COMPARE_MODELS": "model-a,model-b",
    "CODEX_COMPARE_QUESTION": "LoRAとは何ですか？",
    "CODEX_COMPARE_REPEATS": "3",
    "CODEX_COMPARE_REASONING_EFFORT": "medium",
    "CODEX_COMPARE_TIMEOUT_SECONDS": "180",
}


@pytest.fixture
def env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """比較条件だけを含む設定ファイルを返す。"""
    for name in ENV_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(f"{name}={value}" for name, value in ENV_SETTINGS.items()),
        encoding="utf-8",
    )
    return path


def response_events(answer: str) -> str:
    """回答と使用量を含む CLI 出力を返す。

    Args:
        answer: 偽モデルの回答。
    """
    events = [
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": answer},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 120,
                "cached_input_tokens": 100,
                "output_tokens": 30,
            },
        },
    ]
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events)


def fake_cli(
    monkeypatch: pytest.MonkeyPatch, results: Sequence[object]
) -> Mock:
    """バージョン照会と試行の戻り値を用意する。

    Args:
        monkeypatch: テスト用の置換機構。
        results: 試行ごとの戻り値または例外。
    """
    monkeypatch.setattr(
        "app.codex_comparison.shutil.which", Mock(return_value="/fake/codex")
    )
    runner = Mock(
        side_effect=[
            subprocess.CompletedProcess(
                ["codex", "--version"], 0, "codex-cli test\n", ""
            ),
            *results,
        ]
    )
    monkeypatch.setattr("app.codex_comparison.subprocess.run", runner)
    return runner


def test_dotenv_and_environment_precedence(
    env_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """シェルの設定を優先し、残りを .env から取得する。"""
    monkeypatch.setenv("CODEX_COMPARE_QUESTION", "別の質問")
    settings = comparison.load_settings(env_path)
    assert settings.question == "別の質問"
    assert settings.models == ("model-a", "model-b")
    assert settings.repeats == 3
    assert settings.reasoning_effort == "medium"
    assert settings.timeout_seconds == 180


@pytest.mark.parametrize("name", ENV_SETTINGS)
def test_missing_setting_has_no_default(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """各必須設定の欠落を検出する。"""
    for key, value in ENV_SETTINGS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=name):
        comparison.load_settings(tmp_path / "absent.env")


@pytest.mark.parametrize(
    "name,value",
    [
        ("CODEX_COMPARE_MODELS", "model-a"),
        ("CODEX_COMPARE_MODELS", "model-a,model-a"),
        ("CODEX_COMPARE_MODELS", "model-a,"),
        ("CODEX_COMPARE_REPEATS", "0"),
        ("CODEX_COMPARE_REPEATS", "abc"),
        ("CODEX_COMPARE_TIMEOUT_SECONDS", "-1"),
        ("CODEX_COMPARE_QUESTION", " "),
        ("CODEX_COMPARE_REASONING_EFFORT", ""),
    ],
)
def test_invalid_settings(
    name: str, value: str, env_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不正な条件でモデルを実行しない。"""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        comparison.load_settings(env_path)


def test_six_independent_answers_and_settings_are_saved(
    env_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一入力の6回答と個別条件を保存し、環境中の秘密値を含めない。"""
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-secret-for-test")
    results = [
        subprocess.CompletedProcess([], 0, response_events(f"回答{index}"), "")
        for index in range(6)
    ]
    runner = fake_cli(monkeypatch, results)
    output = comparison.run_comparison(
        comparison.load_settings(env_path), tmp_path / "outputs"
    )
    assert (output / "question.txt").read_text() == "LoRAとは何ですか？\n"
    settings = json.loads((output / "settings.json").read_text())
    assert settings["models"] == ["model-a", "model-b"]
    assert settings["codex_version"] == "codex-cli test"
    records = json.loads((output / "summary.json").read_text())
    assert [(record["model"], record["trial"]) for record in records] == [
        (model, trial)
        for trial in range(1, 4)
        for model in ("model-a", "model-b")
    ]
    calls = runner.call_args_list[1:]
    assert len(calls) == 6
    assert len({call.kwargs["cwd"] for call in calls}) == 6
    prompt = (output / "prompt.txt").read_text()
    for index, (record, call) in enumerate(zip(records, calls, strict=True)):
        assert call.kwargs["input"] == prompt
        assert call.kwargs["timeout"] == 180
        command = call.args[0]
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert command[command.index("--model") + 1] == record["model"]
        assert 'model_reasoning_effort="medium"' in command
        assert "--ephemeral" in command
        assert "--ignore-user-config" in command
        assert command[-1] == "-"
        assert record["status"] == "success"
        assert record["usage"]["input_tokens"] == 120
        trial_dir = output / record["directory"]
        assert (trial_dir / "answer.md").read_text() == f"回答{index}"
        assert (
            json.loads((trial_dir / "settings.json").read_text())["model"]
            == record["model"]
        )
        assert json.loads((trial_dir / "result.json").read_text()) == record
    for file in output.rglob("*"):
        if file.is_file():
            assert "not-a-real-secret-for-test" not in file.read_text()


@pytest.mark.parametrize(
    "failure,error",
    [
        (
            subprocess.CompletedProcess([], 1, "", "private error details"),
            "codex_exit_error",
        ),
        (subprocess.TimeoutExpired("codex", 1), "timeout"),
        (OSError("private error details"), "execution_or_storage_error"),
        (
            subprocess.CompletedProcess([], 0, "not json", ""),
            "invalid_response",
        ),
        (
            subprocess.CompletedProcess([], 0, '{"type":"turn.failed"}', ""),
            "invalid_response",
        ),
    ],
)
def test_failed_trial_does_not_prevent_other_answers(
    failure: object,
    error: str,
    env_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失敗を記録し、後続の回答を保存する。"""
    monkeypatch.setenv("CODEX_COMPARE_REPEATS", "1")
    fake_cli(
        monkeypatch,
        [
            failure,
            subprocess.CompletedProcess([], 0, response_events("回答"), ""),
        ],
    )
    output = comparison.run_comparison(
        comparison.load_settings(env_path), tmp_path / "outputs"
    )
    records = json.loads((output / "summary.json").read_text())
    assert records[0]["status"] == "error"
    assert records[0]["error"] == error
    assert not (output / records[0]["directory"] / "answer.md").exists()
    assert records[1]["status"] == "success"
    assert "private error details" not in (output / "summary.json").read_text()


@pytest.mark.parametrize(
    "event",
    [
        "[]",
        "{}",
        '{"type":"turn.completed"}',
        '{"type":"error","message":"private error details"}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"途中"}}',
    ],
)
def test_incomplete_or_failed_events_are_not_answers(event: str) -> None:
    """回答のない終了や途中の出力を成功とみなさない。"""
    with pytest.raises(ValueError):
        comparison.parse_response(event)


def test_external_tool_answer_is_saved_but_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """外部情報を参照した回答を比較から区別する。"""
    tool_event = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "command_execution"},
        }
    )
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            [], 0, tool_event + "\n" + response_events("参照を伴う回答"), ""
        )
    )
    monkeypatch.setattr("app.codex_comparison.subprocess.run", runner)
    record = comparison.run_trial(["codex"], "質問", 1, tmp_path)
    assert record["status"] == "excluded"
    assert record["error"] == "external_tools_used"
    assert record["tool_types"] == ["command_execution"]
    assert (tmp_path / "answer.md").read_text() == "参照を伴う回答"


def test_multiple_experiments_keep_previous_outputs(
    env_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """連続実行で既存の回答を上書きしない。"""
    monkeypatch.setenv("CODEX_COMPARE_REPEATS", "1")
    settings = comparison.load_settings(env_path)
    clock = Mock()
    clock.now.side_effect = [
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=ZoneInfo("Asia/Tokyo")),
        datetime(2026, 1, 2, 3, 4, 6, tzinfo=ZoneInfo("Asia/Tokyo")),
    ]
    monkeypatch.setattr("app.codex_comparison.datetime", clock)
    outputs = []
    for answer in ("回答A", "回答B"):
        fake_cli(
            monkeypatch,
            [subprocess.CompletedProcess([], 0, response_events(answer), "")]
            * 2,
        )
        outputs.append(
            comparison.run_comparison(settings, tmp_path / "outputs")
        )
    assert [output.name for output in outputs] == [
        "20260102-030405",
        "20260102-030406",
    ]
    assert (outputs[0] / "model_01/trial_01/answer.md").read_text() == "回答A"
    assert (outputs[1] / "model_01/trial_01/answer.md").read_text() == "回答B"


def test_same_second_keeps_existing_answers(
    env_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じ秒の再実行は既存の回答を上書きせずにエラーになる。"""
    monkeypatch.setenv("CODEX_COMPARE_REPEATS", "1")
    settings = comparison.load_settings(env_path)
    clock = Mock()
    clock.now.return_value = datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=ZoneInfo("Asia/Tokyo")
    )
    monkeypatch.setattr("app.codex_comparison.datetime", clock)
    fake_cli(
        monkeypatch,
        [subprocess.CompletedProcess([], 0, response_events("回答A"), "")] * 2,
    )
    output = comparison.run_comparison(settings, tmp_path / "outputs")
    saved_files = {
        path.relative_to(output): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    runner = fake_cli(monkeypatch, [])
    with pytest.raises(FileExistsError):
        comparison.run_comparison(settings, tmp_path / "outputs")
    assert runner.call_count == 1
    assert saved_files == {
        path.relative_to(output): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }


def test_missing_cli_fails_before_trials(
    env_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI がない場合に起動条件を通知する。"""
    monkeypatch.setattr(
        "app.codex_comparison.shutil.which", Mock(return_value=None)
    )
    with pytest.raises(RuntimeError, match="Codex CLI"):
        comparison.run_comparison(
            comparison.load_settings(env_path), tmp_path / "outputs"
        )


def test_main_reports_failure_and_output_directory(
    env_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失敗を含む比較は終了コード1と出力先を返す。"""
    monkeypatch.setenv("CODEX_COMPARE_REPEATS", "1")
    monkeypatch.setattr(comparison, "PROJECT_ROOT", tmp_path)
    fake_cli(
        monkeypatch,
        [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, response_events("回答"), ""),
        ],
    )
    with pytest.raises(SystemExit) as exception:
        comparison.main()
    assert exception.value.code == 1
    output = capsys.readouterr().out
    assert str(tmp_path / "outputs") in output
    assert "1/2" in output
