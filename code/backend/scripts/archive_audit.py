"""
审计归档命令行。给定时任务或运维手工补跑用。

跑法（在 code/ 目录下）：
    python backend/scripts/archive_audit.py 2026-11-12          # 归档某一天
    python backend/scripts/archive_audit.py --last 3            # 归档最近 3 天（含今天）
    python backend/scripts/archive_audit.py --verify 2026-11-12 # 重算摘要校验
    python backend/scripts/archive_audit.py --show              # 看当前用的是哪个桶/目录
    python backend/scripts/archive_audit.py --doctor            # 生产自检：桶、对象锁、配置

退出码：
    0  全部成功（或校验通过）
    1  有失败（审计记录读不到、桶写不进、内容被改）
    2  配置问题（缺 SDK / 缺连接串）

注意：归档是旁路。**失败不会影响业务写入**，所以这个脚本适合放在定时任务里，
     按退出码告警，而不是塞进请求链路。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import audit_archive  # noqa: E402


def emit(payload: dict) -> None:
    """统一输出格式：一行一段 JSON，方便定时任务采集。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def parse_date_arg(argv: list[str]) -> str | None:
    """读命令行第一个参数里的 YYYY-MM-DD；不是日期就返回 None。"""
    if not argv:
        return None
    try:
        return date.fromisoformat(argv[0]).isoformat()
    except ValueError:
        return None


def parse_days(argv: list[str]) -> int:
    """读 `--last N` 的 N，至少 1 天。"""
    for index, item in enumerate(argv):
        if item == "--last" and index + 1 < len(argv):
            return max(1, int(argv[index + 1]))
    return 1


def main() -> int:
    """解析参数 → 归档或校验 → 用退出码表达结果。"""
    load_dotenv()
    settings = load_settings()
    argv = sys.argv[1:]

    if not argv or "--show" in argv:
        emit({"mode": "show", **audit_archive.describe(settings)})
        return 0

    if "--doctor" in argv:
        result = audit_archive.diagnose(settings)
        emit({"mode": "doctor", **result})
        return 0 if result.get("ok") else 2

    if "--verify" in argv:
        index = argv.index("--verify")
        if index + 1 >= len(argv):
            emit({"error": "--verify 需要一个 YYYY-MM-DD"})
            return 2
        result = audit_archive.verify_day(settings, argv[index + 1])
        emit(result)
        return 0 if result.get("ok") else 1

    if "--last" in argv:
        days = parse_days(argv)
        offset_list = list(range(days))
    elif (date_arg := parse_date_arg(argv)):
        # 文档里的 `archive_audit.py 2026-11-12` 写法：只归档指定日期。
        offset_list = [date_arg]
    else:
        offset_list = [0]

    exit_code = 0
    for offset in offset_list:
        # 位置日期直接就是目标日；--last 才是相对今天的天数偏移。
        day = offset if isinstance(offset, str) else (
            date.today() - timedelta(days=offset)
        ).isoformat()
        result = audit_archive.archive_day(settings, day)
        emit(result)
        if result.get("archived"):
            continue
        reason = str(result.get("reason", ""))
        # 空日不是故障；配置/依赖问题单独给 2，其余算失败
        if reason.startswith("config_error"):
            exit_code = max(exit_code, 2)
        elif reason != "no_events":
            exit_code = max(exit_code, 1)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
