"""pytest 公共配置：把后端、worker、共享包的目录加进 sys.path。

测试与自检脚本的分工
--------------------
* `tests/` 放**可重复运行的单元测试**（pytest，纯逻辑或本地临时目录，不碰真库）；
* `backend/scripts/selftest_*.py` 放**需要真库/真链路的验收脚本**（连开发库、
  造探针数据再清理），它们是阶段性验收证据，不适合放进 CI 的单元测试阶段。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

CODE_ROOT = Path(__file__).resolve().parents[1]
# 在测试模块 import app.main 之前先留一份干净环境：main.py 模块级
# `app = create_app()` 会调 load_dotenv()，把 code/.env 的 REDIS_URL 等
# 注入进程。若以每个测试开始时的环境为快照，快照本身已经被污染。
_ORIGINAL_ENV = dict(os.environ)

for path in (CODE_ROOT / "backend", CODE_ROOT / "packages",
             CODE_ROOT / "worker", CODE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _restore_env() -> None:
    """恢复 conftest 导入时的环境，不碰 pytest 自己的运行时变量。"""
    for key, value in _ORIGINAL_ENV.items():
        os.environ[key] = value
    for key in list(os.environ):
        if key in _ORIGINAL_ENV:
            continue
        if key.startswith("PYTEST_") or key.startswith("COV_CORE"):
            continue
        os.environ.pop(key, None)


@pytest.fixture(autouse=True)
def _restore_process_env():
    """每个测试前后恢复到 conftest 导入时的环境。

    `create_app()` 会调 `load_dotenv()` 把 `code/.env` 注入进程环境；不隔离的话，
    一个测试里的 REDIS_URL / APP_ENV 会污染后面的执行许可测试，表现为
    「本机没 Redis，却走了 Redis 去重并全部拒绝」。
    """
    _restore_env()
    yield
    _restore_env()


