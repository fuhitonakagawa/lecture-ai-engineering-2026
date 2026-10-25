"""環境設定とデータの参照がプロジェクト内で完結することを検証する。"""

from pathlib import Path

import pytest

from src import tools
from src.config import settings

ENVIRONMENT = {
    "AIE_CODEX_MODEL": "test-model",
    "AIE_CODEX_REASONING_EFFORT": "low",
    "AIE_CODEX_TIMEOUT_SECONDS": "15",
    "AIE_CODEX_MAX_REQUESTS": "3",
    "AIE_CODEX_MAX_ATTEMPTS": "2",
    "AIE_CODEX_RETRY_DELAY_SECONDS": "1",
}


def test_env_file_is_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """別の作業ディレクトリの.envではなく、専用の設定ファイルを読む。"""
    project = tmp_path / "agent"
    project.mkdir()
    (project / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in ENVIRONMENT.items())
    )
    (tmp_path / ".env").write_text("AIE_CODEX_MODEL=unrelated-model\n")
    for key in ENVIRONMENT:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings, "PROJECT_ROOT", project)
    monkeypatch.chdir(tmp_path)
    assert settings.load_codex_settings().model == "test-model"


def test_environment_has_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """環境変数で指定した値を.envより優先する。"""
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".env").write_text("AIE_CODEX_MODEL=file-model\n")
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    assert settings.load_codex_settings().model == "test-model"


@pytest.mark.parametrize("name", ENVIRONMENT)
def test_required_setting_has_no_fallback(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """必須設定がない場合は他プロジェクトの.envで補完しない。"""
    monkeypatch.setattr(settings, "PROJECT_ROOT", tmp_path)
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=name):
        settings.load_codex_settings()


def test_learning_data_belongs_to_project() -> None:
    """用語集と講義資料が独立したプロジェクトに含まれる。"""
    assert tools.DATA_DIR == settings.PROJECT_ROOT / "data"
    assert (tools.DATA_DIR / "glossary.json").is_file()
    assert "LoRA" in tools.get_lecture_summary(2)
