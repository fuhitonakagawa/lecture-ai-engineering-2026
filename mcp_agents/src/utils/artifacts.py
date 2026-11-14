"""比較結果をUTF-8のJSONとして保存する。"""

import json
from pathlib import Path


def write_json(path: Path, data: object) -> None:
    """JSON ファイルを UTF-8 で保存する。

    Args:
        path: 保存先。
        data: JSON に変換可能な値。
    """
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
