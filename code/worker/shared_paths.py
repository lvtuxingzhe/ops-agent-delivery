"""
共享包路径引导 —— 让 worker 能找到 code/packages 下的 ops_guard / ops_contract。

为什么单独一个文件
----------------
这两个包在本地开发时位于 `code/packages`，容器里位于 `/srv/packages`，
位置不同但都要能 import。把「往上找 packages」这件事收在一处，
比在每个模块里各写一遍 sys.path.insert 可控。

它只做路径计算，不 import 任何业务模块，所以可以被任意模块在顶部调用。
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent


def ensure_shared_packages() -> None:
    """把 packages 目录加进 sys.path（幂等）。找不到就让 import 自然失败。"""
    for candidate in (WORKER_DIR.parent / "packages", WORKER_DIR / "packages"):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
