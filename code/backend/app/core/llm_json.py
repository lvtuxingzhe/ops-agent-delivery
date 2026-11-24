"""模型回包的 JSON 提取：去掉 ``` 围栏，取第一个平衡的 JSON 对象。

审批预审和知识准入都会遇到“模型前后带解释”的回包；这里只做一件事，
不夹带任何业务枚举，避免两个服务各自维护一份解析逻辑再慢慢漂移。
"""

from __future__ import annotations

import json
import re
from typing import Any


class JsonExtractError(ValueError):
    """回包里没有可解析的平衡 JSON 对象。"""


def extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    if start < 0:
        raise JsonExtractError("no_json_object")
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(cleaned[start:index + 1])
                except json.JSONDecodeError as exc:
                    raise JsonExtractError("invalid_json") from exc
                if not isinstance(value, dict):
                    raise JsonExtractError("not_json_object")
                return value
    raise JsonExtractError("unbalanced_json")
