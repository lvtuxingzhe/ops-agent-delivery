"""TOTP 两段式绑定与二维码生成。"""

from __future__ import annotations

from ...core.config import Settings
from ...core.db import connect_auth
from ...core.security import (
    SecretBox,
    build_otpauth_uri,
    generate_totp_secret,
    totp_match_counter,
)
from .audit import _audit_login
from .core import AuthError


def totp_status(settings: Settings, user_id: int) -> bool:
    """当前账号是否已经绑定并启用动态口令。"""
    with connect_auth(settings) as conn:
        row = conn.execute(
            "SELECT totp_enabled FROM v3_auth_credentials WHERE user_id = %s",
            (int(user_id),),
        ).fetchone()
    return bool(row and row[0])


def totp_bind_start(settings: Settings, user_id: int, username: str) -> dict:
    """
    生成新密钥存下（未启用状态），返回 otpauth 链接 + 二维码 SVG。

    已绑定过的账号不允许在这里重置：否则任何拿到会话的人都能把 MFA 换绑成
    自己的口令（等于绕过第二道认证）。换绑必须走管理员解绑。
    """
    secret = generate_totp_secret()
    encrypted = SecretBox(settings.totp_encryption_key).encrypt(secret)
    with connect_auth(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT totp_enabled FROM v3_auth_credentials WHERE user_id = %s", (user_id,)
            ).fetchone()
            if row and row[0]:
                _audit_login(settings, "auth.totp.bind_rejected", username, "api",
                             {"reason": "already_bound"})
                raise AuthError("该账号已绑定动态口令；如需换绑先找管理员解绑")
            if row:
                conn.execute(
                    """
                    UPDATE v3_auth_credentials
                       SET totp_secret_enc = %s, totp_enabled = FALSE,
                           totp_last_counter = NULL,
                           totp_failed_attempts = 0,
                           totp_locked_until = NULL,
                           updated_at = now()
                     WHERE user_id = %s
                    """,
                    (encrypted, user_id),
                )
            else:
                # 没有凭据行时补一行，但【密码哈希留空】。
                # 早期版本这里写了一个固定字符串的哈希当占位 —— 那等于给账号
                # 留了一个人人可用的口令。空哈希 + 登录入口明确拒绝才是安全的。
                conn.execute(
                    """
                    INSERT INTO v3_auth_credentials (user_id, password_hash, totp_secret_enc)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, "", encrypted),
                )

    _audit_login(settings, "auth.totp.bind_started", username, "api",
                 {"user_id": user_id})
    uri = build_otpauth_uri(secret, username, settings.auth_totp_issuer)
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


def totp_bind_confirm(settings: Settings, user_id: int, code: str) -> None:
    """用户扫完码输当前口令，对了才真正启用。密钥从此不再回显。"""
    with connect_auth(settings) as conn:
        row = conn.execute(
            "SELECT totp_secret_enc, totp_enabled FROM v3_auth_credentials WHERE user_id = %s",
            (user_id,),
        ).fetchone()
    if not row or not row[0]:
        raise AuthError("请先发起绑定（没有待确认的密钥）")
    if row[1]:
        raise AuthError("该账号已绑定动态口令；如需换绑先找管理员解绑")

    secret = SecretBox(settings.totp_encryption_key).decrypt(row[0])
    counter = totp_match_counter(secret, code)
    if counter is None:
        raise AuthError("口令不对，请确认手机时间和服务器一致后重试")

    with connect_auth(settings) as conn:
        with conn.transaction():
            conn.execute(
                "UPDATE v3_auth_credentials SET totp_enabled = TRUE, "
                "totp_bound_at = now(), totp_last_counter = %s, "
                "totp_failed_attempts = 0, totp_locked_until = NULL "
                "WHERE user_id = %s",
                (int(counter), user_id),
            )
    _audit_login(settings, "auth.totp.bound", str(user_id), "api", {"enabled": True})


def _qr_svg(data: str) -> str:
    """
    把 otpauth 链接画成二维码 SVG（qrcode 库的 SVG 工厂，不需要 PIL）。

    坑：qrcode 的 SVG save() 内部用 ElementTree.write(encoding="UTF-8")，
    会往流里写字节 —— 传 StringIO 直接 TypeError。必须用 BytesIO 接，
    拿到 bytes 再 decode 成字符串返回。
    """
    import io

    import qrcode
    import qrcode.image.svg

    image = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode("utf-8")
