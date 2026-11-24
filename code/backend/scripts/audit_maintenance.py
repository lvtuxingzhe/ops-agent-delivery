"""
审计日常维护：补写缓冲 + 归档 + 校验。给系统定时任务调用。

跑法（在 code/ 目录下）：
    python backend/scripts/audit_maintenance.py                 # 补缓冲 + 归档昨天
    python backend/scripts/audit_maintenance.py --days 7        # 补缓冲 + 归档前 7 天
    python backend/scripts/audit_maintenance.py --today         # 连今天一起归档
    python backend/scripts/audit_maintenance.py --flush-only    # 只补写缓冲

建议排期：每天 00:10 跑一次（日切之后再归档「昨天」，避免拿到半天的数据）。

退出码：
    0  全部成功
    1  有失败项（缓冲补写失败、归档失败、校验不一致）
    2  配置问题（缺连接串 / 缺 MinIO SDK）

为什么不做成进程内调度
----------------------
归档和补写都是旁路：跑在独立进程里，挂了不影响 API，也不会因为
「调度器没起来」把业务请求卡住。这也符合设计里「异步归档」的定稿。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core import audit_v3  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import audit_archive, notifications  # noqa: E402

STATUS_PATH = Path(__file__).resolve().parents[2] / "var" / "audit-archive" / "maintenance-status.json"


def write_status(summary: dict, exit_code: int) -> None:
    """落一份机器可读状态，Prometheus/巡检脚本不用解析整段标准输出。"""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "exit_code": exit_code,
        "summary": summary,
    }
    STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def notify_failure(settings, summary: dict) -> int:
    """归档/补写失败时通知管理员；同一失败日只发一次。"""
    if summary.get("failed"):
        result = summary["failed"][0]
        day = str(result.get("day") or date.today().isoformat())
        reason = str(result.get("reason") or "unknown")
        return notifications.notify_archive_failure(
            settings, day=day, reason=reason, notify_key=day,
        )
    flush = summary.get("flush") or {}
    if "failed_reason" in flush:
        day = date.today().isoformat()
        return notifications.notify_archive_failure(
            settings, day=day, reason=str(flush["failed_reason"]),
            notify_key=f"flush:{day}",
        )
    return 0


def emit(payload: dict) -> None:
    """统一输出：一段 JSON，方便定时任务采集与告警。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def days_to_archive(argv: list[str]) -> list[str]:
    """算出要归档的日期：默认「昨天」，`--today` 时连今天一起，`--days N` 往前推。"""
    count = 1
    for index, item in enumerate(argv):
        if item == "--days" and index + 1 < len(argv):
            count = max(1, int(argv[index + 1]))
    include_today = "--today" in argv
    start = 0 if include_today else 1
    return [(date.today() - timedelta(days=offset)).isoformat()
            for offset in range(start, start + count)]


def main() -> int:
    """补写缓冲 → 逐天归档 → 校验，最后用退出码表达结果。"""
    load_dotenv()
    settings = load_settings()
    argv = sys.argv[1:]

    summary: dict = {"mode": "maintenance", "flush": None, "archived": [], "failed": [], "verified": []}

    # --- 1. 缓冲补写：把排查侧降级落下的那条补进正式审计记录 --------------
    try:
        summary["flush"] = audit_v3.flush_buffer(settings)
    except Exception as exc:  # noqa: BLE001 - 单独一项失败不拖累后面
        summary["flush"] = {"failed_reason": f"{type(exc).__name__}: {exc}"}

    exit_code = 0
    if "--flush-only" in argv:
        emit(summary)
        return 0 if "failed_reason" not in (summary["flush"] or {}) else 1

    # --- 2. 归档 -------------------------------------------------------
    for day in days_to_archive(argv):
        result = audit_archive.archive_day(settings, day)
        if result.get("archived"):
            summary["archived"].append({key: result.get(key) for key in
                                        ("day", "event_count", "bytes", "sha256", "transport")})
            verified = audit_archive.verify_day(settings, day)
            summary["verified"].append(verified)
            if not verified.get("ok"):
                exit_code = max(exit_code, 1)
        elif result.get("reason") == "no_events":
            summary["archived"].append({"day": day, "note": "no_events"})
        else:
            summary["failed"].append(result)
            reason = str(result.get("reason", ""))
            exit_code = max(exit_code, 2 if reason.startswith("config_error") else 1)

    if "failed_reason" in (summary["flush"] or {}):
        exit_code = max(exit_code, 1)

    if exit_code and "--no-notify" not in argv:
        try:
            summary["notified"] = notify_failure(settings, summary)
        except Exception as exc:  # noqa: BLE001 - 通知失败不改归档退出码
            summary["notify_error"] = f"{type(exc).__name__}: {exc}"

    write_status(summary, exit_code)
    emit(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
