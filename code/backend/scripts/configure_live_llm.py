"""
把「真实 DeepSeek」配成默认模型并打开出网白名单（本机环境用）。

背景：默认模型与出网白名单都是显式配置，代码不猜。
本脚本只做两件事：
  1) 建/启用 `deepseek-live` 配置（base_url=https://api.deepseek.com/v1,
     model=deepseek-chat），并设为默认；
  2) 把网关 `llm_gateway/default` 的 domain_allowlist 设为 api.deepseek.com，
     并把 no_real_call 置 false。
密钥不写库、不打印，只从环境变量 LLM_API_KEY 读（由 .env/部署注入）。

跑法（在 code/ 目录下）：
    ./backend/.venv/Scripts/python.exe backend/scripts/configure_live_llm.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.audit import AuditEvent  # noqa: E402
from app.core.audit_v3 import insert_event  # noqa: E402
from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_app  # noqa: E402
from app.services import llm_config_service as llm  # noqa: E402

CONFIG_KEY = "deepseek-live"
BASE_URL = "https://api.deepseek.com/v1"
MODEL_NAME = "deepseek-chat"
ALLOWED_HOSTS = ["api.deepseek.com"]


def main() -> int:
    load_dotenv()
    settings = load_settings()

    existing = llm.get_config(settings, CONFIG_KEY)
    if existing is None:
        llm.create_config(
            settings, name=CONFIG_KEY, template_code="deepseek",
            protocol="openai_compatible", base_url=BASE_URL,
            model_name=MODEL_NAME, credential_ref="LLM_API_KEY",
            params={"temperature": 0.2, "max_tokens": 2048,
                    "timeout_seconds": 60},
            enabled=True, is_default=True, actor_id=1,
            client_source="selftest",
        )
        print(f"[OK] 新建 {CONFIG_KEY} 并设为默认")
    else:
        llm.set_enabled(settings, CONFIG_KEY, True, actor_id=1)
        llm.set_default(settings, CONFIG_KEY, actor_id=1)
        print(f"[OK] 复用 {CONFIG_KEY}，已启用并设为默认")

    with connect_app(settings) as conn:
        with conn.transaction():
            conn.execute(
                """
                UPDATE v3_configs
                   SET value = value || %s::jsonb, updated_at = now()
                 WHERE namespace = 'llm_gateway' AND key = 'default'
                """,
                (json.dumps({"domain_allowlist": ALLOWED_HOSTS,
                             "no_real_call": False}),),
            )
            insert_event(conn, AuditEvent(
                trace_id=f"llm-live-{uuid.uuid4()}",
                event_type="llm.gateway.updated",
                actor="1", client_source="selftest",
                payload={"domain_allowlist": ALLOWED_HOSTS,
                         "no_real_call": False},
            ))
    print(f"[OK] 白名单={ALLOWED_HOSTS} · no_real_call=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
