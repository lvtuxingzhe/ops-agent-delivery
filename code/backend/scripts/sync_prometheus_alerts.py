"""
Prometheus 告警同步：拉取 firing 告警 → 平台站内/飞书/邮件。

跑法（在 code/ 目录下）：
    python backend/scripts/sync_prometheus_alerts.py --once   # 只同步一轮
    python backend/scripts/sync_prometheus_alerts.py          # 常驻，默认 60 秒一轮
    python backend/scripts/sync_prometheus_alerts.py --interval 30

为什么用拉取而不是 Alertmanager webhook：
150 在监控网段，后台在用户本机；让 150 反向连 `127.0.0.1:8000`
既不通也不安全。后台主动读 `/api/v1/alerts`，只读、无入站端口，
和现有数据源适配器共用同一套只读 HTTP 与凭据解析。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import alert_sync  # noqa: E402


def emit(payload: dict) -> None:
    """一行 JSON：便于任务计划/日志采集判断本轮结果。"""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    load_dotenv()
    argv = sys.argv[1:]
    once = "--once" in argv
    interval = 60
    for index, item in enumerate(argv):
        if item == "--interval" and index + 1 < len(argv):
            interval = max(10, int(argv[index + 1]))

    settings = load_settings()
    while True:
        result = alert_sync.sync_once(settings)
        emit(result)
        if once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
