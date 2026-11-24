"""
用户管理链路自检（需要真库，不是 pytest 单元测试）。

覆盖：
  建用户 → 列表可见 → 改角色 → 重置密码 → 重置 TOTP → 绑手机 → 解绑
  → 自我保护 → 最后管理员保护 → 清理临时账号。

不打印密码哈希、TOTP 密钥或完整手机号。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.core.db import connect_user_admin  # noqa: E402
from app.core.security import verify_password  # noqa: E402
from app.services import user_admin_service as service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def password_hash(settings, user_id: int) -> str:
    with connect_user_admin(settings) as conn:
        row = conn.execute(
            "SELECT password_hash FROM v3_auth_credentials WHERE user_id = %s",
            (user_id,),
        ).fetchone()
    return row[0] if row else ""


def cleanup(settings, user_id: int) -> None:
    """只删自检临时账号；级联删凭据和手机绑定，审计保留。"""
    with connect_user_admin(settings) as conn:
        with conn.transaction():
            conn.execute("DELETE FROM v3_users WHERE id = %s", (user_id,))


def main() -> int:
    load_dotenv()
    settings = load_settings()
    username = f"a1_selftest_{secrets.token_hex(4)}"
    phone = "139" + "".join(str(secrets.randbelow(10)) for _ in range(8))
    user_id = 0

    try:
        created = service.create_user(
            settings, username=username, display_name="A1 自检账号",
            roles=["investigator"], phone=phone,
            actor_id=1, client_source="api",
        )
        user_id = int(created["id"])
        record("建用户", bool(created.get("initial_password")), username)
        record("列表可见", any(
            item["username"] == username
            for item in service.list_users(settings, q=username)["items"]
        ))
        record("手机号脱敏", created["phone_masked"] == service.mask_phone(phone))

        updated = service.update_user(
            settings, user_id=user_id, roles=["oncall"],
            actor_id=1, client_source="api",
        )
        record("改角色", updated["roles"] == ["oncall"])

        reset = service.reset_password(
            settings, user_id=user_id, actor_id=1, client_source="api",
        )
        record("重置密码", verify_password(reset["password"], password_hash(settings, user_id)))

        service.reset_totp(settings, user_id=user_id, actor_id=1, client_source="api")
        item = next(
            row for row in service.list_users(settings, q=username)["items"]
            if row["id"] == user_id
        )
        record("重置 TOTP", item["totp_enabled"] is False)

        bound = service.bind_phone(
            settings, user_id=user_id, phone=phone,
            actor_id=1, client_source="api",
        )
        record("绑定手机", bound["phone_masked"] == service.mask_phone(phone))
        unbound = service.unbind_phone(
            settings, user_id=user_id, actor_id=1, client_source="api",
        )
        record("解绑手机", unbound["phone_masked"] == "")

        try:
            service.update_user(
                settings, user_id=1, is_active=False,
                actor_id=1, client_source="api",
            )
            record("自我保护", False, "没有拦住停用自己")
        except service.UserAdminError as exc:
            record("自我保护", exc.code == "SELF_PROTECTED", exc.code)

        try:
            service.update_user(
                settings, user_id=1, roles=["oncall"],
                actor_id=2, client_source="api",
            )
            record("最后管理员保护", False, "没有拦住降权最后管理员")
        except service.UserAdminError as exc:
            record("最后管理员保护", exc.code == "LAST_ADMIN", exc.code)

        deleted = service.delete_user(
            settings, user_id=user_id, actor_id=1, client_source="api",
        )
        record("软删除账号", deleted.get("deleted") is True, username)
        record("软删除后列表隐藏", not any(
            item["username"] == username
            for item in service.list_users(settings, q=username)["items"]
        ))
        try:
            service.create_user(
                settings, username=username, display_name="不应重建",
                roles=["investigator"], phone="",
                actor_id=1, client_source="api",
            )
            record("软删除用户名不可复用", False, "同名账号被重新创建")
        except service.UserAdminError as exc:
            record("软删除用户名不可复用", exc.code == "USERNAME_TAKEN", exc.code)

        # 自检结束：先把软删除的墓碑行物理清掉，避免污染数据库。
        cleanup(settings, user_id)
        user_id = 0
        record("清理临时账号", True)

    except Exception as exc:  # noqa: BLE001 - 自检脚本要把失败原因打出来
        record("自检异常", False, f"{type(exc).__name__}: {exc}")
        if user_id:
            try:
                cleanup(settings, user_id)
            except Exception as cleanup_exc:  # noqa: BLE001
                record("清理临时账号", False, str(cleanup_exc))
        return 1

    failed = [item for item in RESULTS if item[0] == "FAIL"]
    print(f"\n[SUMMARY] {len(RESULTS) - len(failed)} pass / {len(failed)} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
