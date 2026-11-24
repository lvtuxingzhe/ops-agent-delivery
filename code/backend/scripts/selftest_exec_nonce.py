"""
执行许可 nonce 的跨进程去重自检（S4-14）。

怎么用（在 code/ 目录，先起 Redis）：
    python backend/scripts/selftest_exec_nonce.py

测什么：
  * 配了 `REDIS_URL` 时，同一令牌在两个独立进程里各验一次：
    第一个通过，第二个必须 `permit_replayed`；
  * Redis 不可达时必须 `permit_nonce_store_unavailable`（失败关闭，不退回内存）。

Redis 连接串只从环境变量或本机默认地址取，不打印。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "packages"))

from ops_contract.permits import issue_permit  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
CHILD = """
import json, sys
from ops_contract.permits import verify_permit
token, secret, task_id, argv_json, host = sys.argv[1:6]
print(json.dumps(verify_permit(
    token, secret=secret, task_id=task_id, platform="linux",
    argv=json.loads(argv_json), host=host,
)))
"""

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def _verify(token: str, secret: str, task_id: str, argv: list[str],
            host: str, redis_url: str) -> tuple[bool, str, str]:
    env = {**os.environ, "REDIS_URL": redis_url,
           "PYTHONPATH": str(CODE / "packages")}
    completed = subprocess.run(
        [sys.executable, "-c", CHILD, token, secret, task_id,
         json.dumps(argv), host],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
    )
    if completed.returncode != 0:
        return False, f"child_failed:{completed.stderr[-200:]}", ""
    return tuple(json.loads(completed.stdout.strip()))  # type: ignore[return-value]


def main() -> int:
    secret = secrets.token_hex(32)
    task_id = "selftest-nonce-" + secrets.token_hex(4)
    argv = ["uname", "-a"]
    host = "vm-150"
    token = issue_permit(secret, task_id=task_id, platform="linux",
                         argv=argv, host=host)

    first = _verify(token, secret, task_id, argv, host, REDIS_URL)
    record("Redis 首次验签通过", first[0] is True, str(first[1]))
    second = _verify(token, secret, task_id, argv, host, REDIS_URL)
    record("第二个进程被拒为重放", second[0] is False
           and second[1] == "permit_replayed", str(second[1]))

    unavailable = _verify(token, secret, task_id, argv, host,
                          "redis://127.0.0.1:6399/0")
    record("Redis 不可达时失败关闭", unavailable[0] is False
           and unavailable[1] == "permit_nonce_store_unavailable",
           str(unavailable[1]))

    failed = [row for row in RESULTS if row[0] == "FAIL"]
    print(f"\nPASS {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
