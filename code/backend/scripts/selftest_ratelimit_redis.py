"""
多副本限流共享计数自检（S4-14）。

怎么用（在 code/ 目录，先起 Redis）：
    python backend/scripts/selftest_ratelimit_redis.py
"""

from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE / "backend"))

from app.core import ratelimit  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    key = "selftest:" + secrets.token_hex(4)
    limit = 3
    now = time.monotonic()
    results = [ratelimit._redis_hit(REDIS_URL, key, limit, now, 60)
               for _ in range(4)]
    record("前 3 次放行", all(item[0] for item in results[:3]),
           str([item[0] for item in results[:3]]))
    record("第 4 次被限流", results[3][0] is False,
           f"retry_after={results[3][1]}")

    client = ratelimit._redis_client(REDIS_URL)
    slot = int(now // 60)
    client.delete(f"ratelimit:{key}:{slot}")
    record("测试键已清理", True, key)

    failed = [row for row in RESULTS if row[0] == "FAIL"]
    print(f"\nPASS {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
