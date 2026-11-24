"""签发一次性执行许可。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT / "worker") not in sys.path:
    sys.path.insert(0, str(CODE_ROOT / "worker"))

from tools.exec_core import (  # noqa: E402
    ExecConfigError,
    fingerprint,
    issue_permit,
)


def main() -> int:
    """解析参数 → 校验 → 打印令牌。密钥缺失或过短直接失败。"""
    parser = argparse.ArgumentParser(description="签发一次性执行许可")
    parser.add_argument("--task-id", required=True, help="绑定的任务号")
    parser.add_argument("--platform", required=True, choices=("linux", "windows"))
    parser.add_argument("--argv-json", required=True, help='命令参数 JSON 数组，如 ["uname","-a"]')
    parser.add_argument("--scope", default="read", choices=("read", "dangerous", "admin"))
    parser.add_argument("--ttl", type=int, default=300, help="有效期秒数，默认 300")
    args = parser.parse_args()

    secret = os.environ.get("EXEC_PERMIT_SECRET", "")
    try:
        argv = json.loads(args.argv_json)
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError("argv-json 必须是字符串数组")
        token = issue_permit(
            secret, task_id=args.task_id, platform=args.platform,
            argv=argv, scope=args.scope, ttl_s=args.ttl,
        )
    except (ExecConfigError, ValueError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "task_id": args.task_id,
        "platform": args.platform,
        "fingerprint": fingerprint(args.platform, argv),
        "scope": args.scope,
        "permit": token,
        "note": "令牌绑定「平台 + 逐参数」，换命令即失效；只从环境变量读密钥",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
