"""密码、TOTP、加密与会话令牌的安全原语。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

# =============================================================================
# 密码：PBKDF2 加盐哈希
# =============================================================================

# 哈希串格式：算法$轮数$盐(hex)$哈希(hex)。自带算法名 → 以后升级可共存。
_PW_ALGO = "pbkdf2_sha256"


def hash_password(plain: str, *, rounds: int = 100000) -> str:
    """把明文密码变成可存储的哈希串。盐用 secrets（密码学随机）独立生成。"""
    if not plain:
        raise ValueError("空密码拒绝哈希：这是调用方 bug，别把空字符串存进去")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return f"{_PW_ALGO}${rounds}${salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """
    校验密码。恒定时间比较（hmac.compare_digest），不因为「前 8 字节一样」
    就提前返回，不给时序攻击留出入口。

    存储串里的轮数自己带：老数据用老轮数验，新数据用新轮数，互不影响。
    """
    try:
        algo, str_rounds, salt_hex, hash_hex = stored.split("$")
        if algo != _PW_ALGO:
            return False
        rounds = int(str_rounds)
        digest = hashlib.pbkdf2_hmac(
            "sha256", plain.encode("utf-8"), bytes.fromhex(salt_hex), rounds
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        # 存储串格式不对 = 校验失败，不算异常，内部格式细节不抛给调用方
        return False


# =============================================================================
# TOTP：RFC 6238（自己实现，可用官方测试向量验证）
# =============================================================================

_TOTP_STEP = 30  # 口令多少秒换一次
_TOTP_DIGITS = 6


def _totp_at(secret_b32: str, counter: int) -> str:
    """按计数器算一个 TOTP 值。counter = floor(时间 / 30)。"""
    key = base64.b32decode(secret_b32, casefold=True)
    msg = struct.pack(">Q", counter)  # HOTP 要求 8 字节大端计数器
    mac = hmac.new(key, msg, hashlib.sha1).digest()
    # 动态截断：RFC 4226 的标准步骤，偏移量取最后一个字节的低 4 位
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** _TOTP_DIGITS)
    return f"{code:0{_TOTP_DIGITS}d}"


def totp_now(secret_b32: str) -> str:
    """算当前时刻的口令。绑定流程「绑定时露一次」和自检都要用它。"""
    return _totp_at(secret_b32, int(time.time()) // _TOTP_STEP)


def totp_match_counter(
    secret_b32: str, code: str, *, window: int = 1,
) -> int | None:
    """
    校验用户输入的口令，返回命中的时间计数器；不匹配返回 None。

    window=1 表示前后各放宽一个 30 秒 —— 手机时间和服务器差半分钟很常见，
    卡死用户没意义；放宽更多不行。返回计数器供调用方做一次性消费。
    """
    if not code or not code.strip().isdigit() or len(code.strip()) != _TOTP_DIGITS:
        return None
    counter = int(time.time()) // _TOTP_STEP
    for drift in range(-window, window + 1):
        expected = _totp_at(secret_b32, counter + drift)
        if hmac.compare_digest(expected, code.strip()):
            return counter + drift
    return None


def totp_verify(secret_b32: str, code: str, *, window: int = 1) -> bool:
    """兼容旧调用：只返回是否命中，不做一次性消费。"""
    return totp_match_counter(secret_b32, code, window=window) is not None


def generate_totp_secret() -> str:
    """生成一个新的 TOTP 密钥（Base32）。160 位，RFC 4226 推荐长度。"""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def build_otpauth_uri(secret_b32: str, username: str, issuer: str) -> str:
    """
    生成扫码绑定用的 otpauth:// 链接（Google Authenticator 等的通用格式）。
    注意 username / issuer 要 URL 编码，里面带空格或冒号会坏。
    """
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(username)}"
        f"?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1"
        f"&digits={_TOTP_DIGITS}&period={_TOTP_STEP}"
    )


# =============================================================================
# 加密：Fernet（TOTP 密钥等小段秘密的对称加密）
# =============================================================================

class SecretBox:
    """
    Fernet 加密的小盒子。key 来自环境变量（默认假值），32 字节 base64。

    为什么用 Fernet 不自己写加密：自己写的一定错。Fernet = AES-128-CBC +
    HMAC + 时间戳，cryptography 库的成品，拿来就用。
    """

    def __init__(self, key_b64: str):
        """初始化加密器或令牌签发器。"""
        if not key_b64:
            raise ValueError("缺少加密密钥（TOTP_ENCRYPTION_KEY），拒绝不加密写入数据库")
        self._fernet = Fernet(key_b64.encode("ascii") if isinstance(key_b64, str) else key_b64)

    def encrypt(self, plaintext: str) -> str:
        """用 Fernet 加密一段文本，返回密文。"""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """解开密文，失败时抛出明确的解密错误。"""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            # 报错不带任何原文内容 —— 解密失败的原因对外就一句话
            raise RuntimeError("密文解密失败：key 不匹配或数据损坏") from exc


# =============================================================================
# 会话令牌：JWT（HS256）
# =============================================================================

class TokenIssuer:
    """登录会话令牌的签发与校验。签名密钥来自环境变量，短时有效。"""

    def __init__(self, secret: str, ttl_minutes: int, issuer: str):
        """初始化加密器或令牌签发器。"""
        if not secret:
            raise ValueError("缺少 AUTH_TOKEN_SECRET，拒绝签发令牌")
        self._secret = secret.encode("utf-8")
        self._ttl_minutes = ttl_minutes
        self._issuer = issuer

    def issue(self, user_id: int) -> str:
        """
        签发令牌。claims 里只有：谁（sub）、谁签的（iss）、什么时候过期（exp）。
        【故意不放角色和用户名】—— 每次请求都重新查库，改角色立刻生效。
        """
        now = int(time.time())
        return jwt.encode(
            {"sub": str(user_id), "iss": self._issuer, "iat": now, "exp": now + self._ttl_minutes * 60},
            self._secret,
            algorithm="HS256",
        )

    def verify(self, token: str) -> int | None:
        """
        验令牌，成功返回 user_id，失败返回 None（过期 / 伪造 / 格式错）。
        只在认证服务里调，拿到 user_id 后还要去库里查 is_active ——
        令牌有效 ≠ 账号还能用（被禁用的账号令牌立刻失效）。
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token, self._secret, algorithms=["HS256"], issuer=self._issuer
            )
            return int(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
            return None


# =============================================================================
# 一次性随机数：短信验证码、确认令牌
# =============================================================================

def generate_sms_code() -> str:
    """6 位数字验证码。用 secrets（密码学随机），randint 不够随机。"""
    return f"{secrets.randbelow(10 ** 6):06d}"


def generate_one_time_token() -> str:
    """
    一次性确认令牌：32 字节随机数的 hex。
    足够长且不可猜测；数据库里只存它的哈希。
    """
    return secrets.token_hex(32)


def sha256_hex(text: str) -> str:
    """算 SHA256 的十六进制。用于把验证码 / 一次性令牌转成库里存的哈希。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
