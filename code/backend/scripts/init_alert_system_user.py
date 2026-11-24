"""
初始化告警自动建单的系统账号。

跑法（在 code/ 目录下）：
    python backend/scripts/init_alert_system_user.py

它做什么：
    * 幂等写入 v3_users：username=system-alert，角色 investigator，启用；
    * 故意不写 v3_auth_credentials，因此这个账号不能登录；
    * 页面侧 update_user / reset_password / reset_totp 已禁止改 system- 前缀账号。

为什么单独一个脚本：
    系统账号只给告警入口做 actor，不应该有密码、手机号或 MFA 绑定；
    用普通建号接口会生成一次性密码，等于给自动化开了一个可登录入口。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_user_admin  # noqa: E402

SYSTEM_USERNAME = "system-alert"
SYSTEM_DISPLAY_NAME = "系统告警"


def main() -> int:
    load_dotenv()
    settings = load_settings()
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT id, roles, is_active FROM v3_users "
                "WHERE lower(username) = lower(%s)",
                (SYSTEM_USERNAME,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE v3_users SET display_name = %s, roles = %s::text[], "
                    "is_active = true WHERE id = %s",
                    (SYSTEM_DISPLAY_NAME, ["investigator"], int(row[0])),
                )
                user_id = int(row[0])
                action = "updated"
            else:
                user_id = int(conn.execute(
                    "INSERT INTO v3_users (username, display_name, roles, is_active) "
                    "VALUES (%s, %s, %s::text[], true) RETURNING id",
                    (SYSTEM_USERNAME, SYSTEM_DISPLAY_NAME, ["investigator"]),
                ).fetchone()[0])
                action = "created"
            # 双保险：系统账号绝不能有可登录凭据。
            conn.execute(
                "DELETE FROM v3_auth_credentials WHERE user_id = %s", (user_id,)
            )
    print(f"[PASS] {action} {SYSTEM_USERNAME} id={user_id} roles=investigator credentials=none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
