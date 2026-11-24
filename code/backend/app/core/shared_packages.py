"""
共享包定位 —— 后台如何找到 code/packages 下的 ops_guard / ops_contract。

为什么单独一个文件
----------------
本地开发时共享包在 `code/packages`，容器里在 `/srv/packages`（compose 只读挂载）。
两处都要能 import，而「逐级向上找 packages」这段逻辑如果散在每个模块里，
迟早有人漏写一次、静默退化成「不脱敏 / 不校验」。

这里只做路径计算，不 import 任何业务模块；找不到就抛错，绝不静默降级。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 从 core/ 往上找，第一个含 packages 子目录的祖先即共享包根
PACKAGES_DIR = next(
    (
        parent / "packages"
        for parent in Path(__file__).resolve().parents
        if (parent / "packages").is_dir()
    ),
    None,
)

if PACKAGES_DIR is None:
    raise ImportError("找不到共享包目录 packages/（需要其中的 ops_guard / ops_contract）")
if str(PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGES_DIR))
