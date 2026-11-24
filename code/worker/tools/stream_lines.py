"""命令输出分行的共享规则。

`\r` 既可能是进度条刷新，也可能是 `\r\n` 的一部分。统一先归一化再按 `\n`
切分，避免 `yum`、`curl` 这类进度输出一直卡在缓冲区里不回调。
"""

from __future__ import annotations


def split_stream_chunk(buffer: str, text: str) -> tuple[list[str], str]:
    """返回 (已完整行, 新缓冲区)；`\r\n` 和单独的 `\r` 都当作换行。"""
    combined = f"{buffer}{text}".replace("\r\n", "\n").replace("\r", "\n")
    parts = combined.split("\n")
    return parts[:-1], parts[-1]
