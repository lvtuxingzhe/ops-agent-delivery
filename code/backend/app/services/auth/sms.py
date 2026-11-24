"""短信验证码发送与限频。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ...core.config import Settings
from ...core.db import connect_auth
from ...core.security import generate_sms_code, sha256_hex
from ...core.sms import get_sms_channel
from .audit import _audit_login
from .core import AuthError

logger = logging.getLogger(__name__)


class SmsRateLimited(AuthError):
    """短信发送频率超限；API 层映射为 429。"""


class SmsChannelUnavailable(AuthError):
    """短信通道未启用或配置错误；API 层映射为 503。"""


def _discard_sms_code(settings: Settings, trace_id: str) -> None:
    """发送失败时撤回刚写入的验证码，避免占掉一分钟/每日额度。"""
    try:
        with connect_auth(settings) as conn:
            conn.execute("DELETE FROM v3_sms_codes WHERE trace_id = %s", (trace_id,))
    except Exception:  # noqa: BLE001 - 清理失败不能盖住真正的发送错误
        logger.exception("清理未发出短信验证码失败 trace_id=%s", trace_id)



def send_sms_code(settings: Settings, phone: str, client_source: str) -> dict:
    """
    发一条验证码。防刷规则：
      * 同一手机号 60 秒内只发 1 条
      * 同一手机号 24 小时最多 5 条
      * 码 6 位、5 分钟有效、一次性、试错 5 次作废（校验在 _login_with_sms）
    """
    with connect_auth(settings) as conn:
        # 限频必须和「写入」在同一个事务里，并对同一手机号加事务级锁。
        # 否则两个并发请求都会读到「0 条」，各自插入一条，一分钟两条的限制形同虚设。
        with conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"sms-send:{phone}",),
            )
            recent = conn.execute(
                """
                SELECT
                  count(*) FILTER (WHERE created_at > now() - interval '1 minute'),
                  count(*) FILTER (WHERE created_at > now() - interval '24 hours')
                FROM v3_sms_codes WHERE phone = %s
                """,
                (phone,),
            ).fetchone()
            if recent[0] >= 1:
                raise SmsRateLimited("发送太频繁，请一分钟后再试")
            if recent[1] >= 5:
                _audit_login(settings, "auth.sms.rate_limited", phone, client_source,
                             {"reason": "daily_limit"})
                raise SmsRateLimited("今日发送次数已达上限")

            code = generate_sms_code()
            trace_id = f"sms-{uuid.uuid4()}"
            conn.execute(
                """
                INSERT INTO v3_sms_codes (phone, code_hash, trace_id, expires_at)
                VALUES (%s, %s, %s, now() + (%s || ' seconds')::interval)
                """,
                (phone, sha256_hex(code), trace_id, str(settings.sms_code_ttl_seconds)),
            )

    # 通道发送放在事务外：发送失败要把刚写入的验证码撤回，
    # 否则下一次请求会被「一分钟一条」挡住，码本身也留在库里。
    try:
        get_sms_channel(settings).send_code(phone, code, trace_id)
    except RuntimeError as exc:
        _discard_sms_code(settings, trace_id)
        raise SmsChannelUnavailable("短信通道未启用，请改用账号密码 + TOTP 登录") from exc
    except Exception:
        _discard_sms_code(settings, trace_id)
        raise
    _audit_login(settings, "auth.sms.sent", phone, client_source,
                 {"ttl_seconds": settings.sms_code_ttl_seconds})

    result: dict[str, Any] = {"sent": True, "ttl_seconds": settings.sms_code_ttl_seconds}
    # 回显只在「测试环境 + 测试通道」同时成立时发生。
    # 少了环境这一道，运维把 SMS_CHANNEL=test 带进生产就等于开放登录。
    if settings.environment == "test" and settings.sms_channel == "test":
        result["dev_code"] = code
        result["dev_note"] = "测试通道回显，仅测试环境可见；production 环境不会出现该字段"
    return result
