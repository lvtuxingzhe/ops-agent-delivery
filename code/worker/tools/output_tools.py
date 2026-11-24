"""长输出回读工具 —— 与具体数据源解耦，只认 handle。

模型在工具回包里看到 `output_ref` 后，用这里的工具按行读取或搜索；
不接受文件路径，也不会跨任务访问。
"""

from __future__ import annotations

import json
from typing import Any

from tools.output_store import OutputStore, OutputStoreError
from tools.registry import evidence_tool


class OutputReadTools:
    """绑定一个任务的 OutputStore；只读，不新增取证。"""

    def __init__(self, store: OutputStore) -> None:
        self.store = store

    @evidence_tool(source="output")
    def read_output(self, handle: str, start_line: int = 1,
                    max_lines: int = 100) -> str:
        """按行读取已保存的长输出。

        handle 来自工具回包里的 output_ref；start_line 从 1 开始；
        max_lines 最多 500，单次内容最多 4000 字符。返回 next_start_line，
        为空表示已经到文件末尾。
        """
        try:
            result = self.store.read(
                str(handle or ""), start_line=start_line, max_lines=max_lines,
            )
        except OutputStoreError:
            return json.dumps({
                "ok": False, "reason": "output_not_found", "handle": str(handle or ""),
            }, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    @evidence_tool(source="output")
    def grep_output(self, handle: str, pattern: str,
                    context: int = 2, max_matches: int = 20) -> str:
        """在已保存的长输出里搜索，返回命中行和上下文。

        handle 来自 output_ref；pattern 是正则，最多 200 字符；
        最多返回 50 条命中，不返回整份文件。
        """
        try:
            result = self.store.grep(
                str(handle or ""), str(pattern or ""),
                context=context, max_matches=max_matches,
            )
        except OutputStoreError:
            return json.dumps({
                "ok": False, "reason": "output_not_found", "handle": str(handle or ""),
            }, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)


def build_output_tools(store: OutputStore) -> list[Any]:
    """装配回读工具；没有 store 就不装。"""
    if store is None:
        return []
    return [OutputReadTools(store)]


__all__ = ["OutputReadTools", "build_output_tools"]
