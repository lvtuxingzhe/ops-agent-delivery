"""
审计维护脚本自检（离线，不连库、不连对象存储）。

跑法（在 code/ 目录下）：
    python backend/scripts/selftest_audit_maintenance.py

做法：把脚本里的两个副作用（补写缓冲、归档）替换成桩，只验证
「日期窗口怎么算」和「结果怎么归类到退出码」——这层逻辑最容易写错，
而真正的读写已由 selftest_audit_archive.py 覆盖。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0]))

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    """和别的自检脚本保持一样的输出风格，方便对照。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def load_script():
    """按文件路径加载维护脚本模块（它只在 __main__ 下才会跑 main）。"""
    spec = importlib.util.spec_from_file_location(
        "audit_maintenance", HERE / "audit_maintenance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module, argv: list[str], *, flush=None, archive=None, verify=None) -> int:
    """替换副作用后跑一次 main，返回退出码（脚本自己的 JSON 输出被静默）。"""
    module.audit_v3.flush_buffer = flush or (lambda _settings: {
        "pending": 0, "flushed": 0, "failed": 0})
    module.audit_archive.archive_day = archive or (lambda _settings, _day: {
        "archived": True, "day": _day, "event_count": 1, "bytes": 2,
        "sha256": "00", "transport": "file"})
    module.audit_archive.verify_day = verify or (lambda _settings, _day: {"ok": True})
    original = sys.argv
    sys.argv = ["audit_maintenance.py", *argv]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return module.main()
    finally:
        sys.argv = original


def main() -> int:
    """跑完所有检查，打印汇总并返回退出码。"""
    print("=" * 66)
    print("审计维护脚本自检（离线：桩替换补写与归档）")
    print("=" * 66)

    module = load_script()

    # --- 1. 日期窗口 --------------------------------------------------
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()
    record("默认只归档昨天", module.days_to_archive([]) == [yesterday],
           str(module.days_to_archive([])))
    record("--today 连今天一起",
           module.days_to_archive(["--today"]) == [today.isoformat()],
           str(module.days_to_archive(["--today"])))
    record("--days 3 从昨天往前推三天",
           module.days_to_archive(["--days", "3"]) == [
               (today - timedelta(days=1)).isoformat(),
               (today - timedelta(days=2)).isoformat(),
               (today - timedelta(days=3)).isoformat(),
           ],
           str(module.days_to_archive(["--days", "3"])))
    record("--days 0 被夹到最少 1 天",
           len(module.days_to_archive(["--days", "0"])) == 1,
           str(module.days_to_archive(["--days", "0"])))

    # --- 2. 退出码归类 -------------------------------------------------
    record("全部成功 → 0", run_main(module, []) == 0, "flush 0 / archive ok / verify ok")
    record("当天没有事件不算失败 → 0",
           run_main(module, [], archive=lambda _s, day: {
               "archived": False, "reason": "no_events", "day": day}) == 0,
           "no_events")
    record("归档失败 → 1",
           run_main(module, [], archive=lambda _s, day: {
               "archived": False, "reason": "put_failed:RuntimeError", "day": day}) == 1,
           "put_failed")
    record("配置问题 → 2",
           run_main(module, [], archive=lambda _s, day: {
               "archived": False, "reason": "config_error:未安装 minio SDK", "day": day}) == 2,
           "config_error")
    record("校验不一致 → 1",
           run_main(module, [], verify=lambda _s, _day: {
               "ok": False, "reason": "content_mismatch"}) == 1,
           "content_mismatch")

    def boom_flush(_settings):
        """模拟缓冲表读不出来。"""
        raise RuntimeError("缓冲不可用")

    record("补写缓冲失败 → 1",
           run_main(module, [], flush=boom_flush) == 1, "flush 抛异常")

    record("--flush-only 不跑归档 → 0",
           run_main(module, ["--flush-only"]) == 0, "只补写")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
