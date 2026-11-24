"""只读审计：列出手工知识里可能的目标主机/服务，供人工确认后回填。

不写数据库、不自动猜写。用法：
    .\\backend\\.venv\\Scripts\\python.exe backend\\scripts\\audit_knowledge_targets.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
VM_RE = re.compile(r"\b(?:vm|host)-?\d{2,}\b", re.IGNORECASE)
SERVICE_RE = re.compile(
    r"\b(nginx|mysql|redis|keepalived|elasticsearch|prometheus|grafana|"
    r"postfix|sshd|k3s|docker|crond|httpd|gitlab|node_exporter)\b",
    re.IGNORECASE,
)


def main() -> int:
    load_dotenv()
    settings = load_settings()
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT id::text, title, body, target_hosts, target_services
              FROM v3_knowledge_entries
             WHERE deleted_at IS NULL
             ORDER BY updated_at DESC
            """
        ).fetchall()

    print(f"active entries: {len(rows)}")
    for row in rows:
        entry_id, title, body, hosts, services = row
        text = f"{title or ''}\n{body or ''}"
        suggested_hosts = sorted(set(IP_RE.findall(text) + VM_RE.findall(text)))
        suggested_services = sorted({item.lower() for item in SERVICE_RE.findall(text)})
        print(f"\n- manual-{entry_id}")
        print(f"  title: {title}")
        print(f"  current_hosts: {list(hosts or [])}")
        print(f"  current_services: {list(services or [])}")
        print(f"  suggested_hosts: {suggested_hosts}")
        print(f"  suggested_services: {suggested_services}")
    print("\n只读输出；确认后请在知识库编辑页手工保存，不要直接改库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
