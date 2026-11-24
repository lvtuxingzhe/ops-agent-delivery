"""主机密码加密。密钥复用认证模块的 TOTP_ENCRYPTION_KEY。"""

from __future__ import annotations

from ...core.config import Settings
from ...core.security import SecretBox
from .errors import HostError


def seal_password(settings: Settings, password: str) -> str:
    """
    把登录密码加密成可以写入数据库的密文。

    密钥复用认证模块的 `TOTP_ENCRYPTION_KEY`：它本来就在做「敏感值写入数据库前必须加
    密」这件事，不另外造第二把密钥，避免部署时多一个必填项。密钥缺失
    时明确报错，绝不退化成明文存储。
    """
    password = str(password or "")
    if not password:
        return ""
    try:
        return SecretBox(settings.totp_encryption_key).encrypt(password)
    except ValueError as exc:
        raise HostError(
            "服务端未配置密码加密密钥（TOTP_ENCRYPTION_KEY），不能保存明文密码",
        ) from exc


def reveal_password(settings: Settings, sealed: str) -> str:
    """把数据库里的密码密文解回明文，只在查看密码接口里用。"""
    if not str(sealed or ""):
        raise HostError("host_password_not_configured")
    try:
        return SecretBox(settings.totp_encryption_key).decrypt(str(sealed))
    except (RuntimeError, ValueError) as exc:
        raise HostError(
            "密码解密失败（可能是服务端加密密钥已更换），请重新保存一次密码",
        ) from exc


__all__ = ["reveal_password", "seal_password"]
