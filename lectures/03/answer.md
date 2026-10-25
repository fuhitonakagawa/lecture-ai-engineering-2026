# 第3回 解答

## 演習

### 実験条件

実行手順と設定は [専用README](../../adk_agents/README.md) に記載する。

Gemini APIキーを使わず、ログイン済みのCodex CLIをモデルの呼び出し先とする。モデルは `gpt-6.1-sol`、推論の強さは `medium`。Pydantic AIとGoogle ADKの両方が、同じCodex接続を使う。

比較する質問は次の4問。各方式・各質問は独立した会話として扱う。

1. 第1回で使った「LoRAとは何ですか？」
2. 「AIE2026の講座で使う『lecNN-start タグ』とは何ですか？」
3. 「LoRAとQLoRAの違いは？」
4. 「第2回の内容から確認問題を1問出して」

素の応答にはツールを渡さず、エージェントには用語集検索、講義要約、確認問題の3ツールを渡す。回答の違いは各方式の `answer.md`、実際の関数名・引数・返り値は `trace.json`、モデル要求回数は `result.json` の `requests` で比較する。

ここでの `requests` はフレームワークからモデルへの要求数であり、Codex execの起動回数と一致する。Codex内部の通信回数は観測対象に含めない。

### 確認問題ツール

`make_quiz(lecture: int)` は指定した回の用語から1つを選び、問題・用語・正解・講義回をJSONで返す。正解は用語集の定義と一致する。用語がない回では、別の回を指定する案内を返す。

docstringは「指定した回の理解を確かめる確認問題と、その正解を返す」「復習や理解度確認を求められたときに使う」と説明する。指示文にも、確認問題を求められたら `make_quiz` を使うことを含める。

APIを使わないテストでは、Pydantic AI・ADKの双方について、`make_quiz({"lecture": 2})` の実行、用語集にある「事前学習」の正解の返却、その結果を含む次のモデル要求を検証した。このテストのモデル要求回数は2回だが、モデルの応答をテスト側で指定した値なので、実モデルで観測した回数とは区別する。

### 実測上の制約

この作業環境でのCodex呼び出しは、内部app-serverの起動時に `Operation not permitted` となる。したがって、実モデルの回答差、実際に選ばれた関数と引数、実測の `requests` を結論として示す根拠はない。テスト用の回答や回数を実測結果として扱わない。

ローカルの通常のターミナルで `adk_agents/` に移動し、`AIE_AGENT_MODE=compare uv run python -m src.main` を実行すると、4問×3方式の回答とトレースが `adk_agents/output/` の実行ごとのフォルダに保存される。単独の質問は `adk_agents/` 内で `AIE_AGENT_MODE=local uv run python -m src.main` で実行し、`adk_agents/.env` の `AIE_TRACE=true` でトレースを表示する。

## 宿題

- 取り組んだレベル：発展(a)。Pydantic AIと同じ学習支援エージェントをGoogle ADKで構成し、コードの書き方とWebでの確認方法を比較する。
- 対象コード：[Pydantic AI版](../../adk_agents/src/agent.py)、[ADK版](../../adk_agents/src/adk_agent.py)、[Codex接続](../../adk_agents/src/codex_backend.py)、[比較実験](../../adk_agents/src/pipeline.py)。

### 共通点と相違点

| 観点 | Pydantic AI | Google ADK |
| --- | --- | --- |
| エージェント | `Agent` | `LlmAgent` |
| 指示文 | `instructions` に指定 | 同じ文面を `instruction` に指定 |
| ツール登録 | `agent.tool_plain(...)` | `tools=[...]` |
| Codex接続 | `FunctionModel` の応答を `ModelResponse` として返す | `BaseLlm` の応答を `LlmResponse` として返す |
| 関数呼び出し | `ToolCallPart` / `ToolReturnPart` | `function_call` / `function_response` |
| 会話の実行 | `Agent.run` | セッションを作り `Runner.run_async` |
| 観測 | メッセージ履歴、ツール名・引数・返り値、使用量 | イベント履歴、セッション、エージェントのグラフ情報 |
| 実行上限 | `UsageLimits` とCodex接続側の制限 | `RunConfig`、モデル前コールバック、Codex接続側の制限 |

モデルの入力には指示・会話履歴・関数定義を含め、Codexの構造化出力から次の関数呼び出しか最終回答を受け取る。関数の実行と次のモデル要求は各フレームワークが担当する。この接続はCodexの組み込みツールを使う方式とは異なり、組み込みツールの利用が検出された応答は比較から除外する。

同じモデル・指示・関数でも、履歴や関数定義の表現はフレームワークごとに異なる。そのため、入力が完全に同一の実験とはみなさず、回答の内容とツールの選択を観察する。

### ADK Web

`adk_agents/` 内で以下を実行し、ブラウザで `http://127.0.0.1:8765` を開く。

```bash
PYTHONPATH=. uv run adk web agents --host 127.0.0.1 --port 8765
```

`study_agent` を選択し、比較対象の質問を入力する。回答に加え、セッション内の関数呼び出しと返り値を読み、CLIの `trace.json` と対応付ける。

HTTP経由では、Webページの応答、`study_agent` の一覧表示用データ、用語集検索・講義要約・確認問題の3ツールを含むグラフ情報を確認した。この作業環境には操作可能なブラウザ接続がないため、画面の見え方や画面上での質問・回答の操作は観測していない。

### 考察の観点

Pydantic AIは、関数登録から会話の実行までを短いコードで表現できる。ADKはエージェント、セッション、実行イベントを別のオブジェクトとして扱うため、会話やツール実行をどこで観測するかが明示的になる。どちらもモデルが関数を選び、プログラムが実行して結果を返すループを持つ。

比較では、単に回答できたかだけでなく、講座固有の質問で用語集を検索したか、LoRAとQLoRAをそれぞれ適切な引数で検索したか、確認問題で指定した回を使ったかを確認する。複数回のモデル要求には時間と利用量の増加が伴うため、ツール選択の適切さと要求回数を併せて考察する。
