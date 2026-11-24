"""age 密钥轮换脚本的编码回归测试。

这里只验证 bech32 编码/解码与公私钥对应关系，不读写真实 `secrets.encrypted.env`，
也不执行 SOPS 或任何文件轮换。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "backup" / "rotate_age_key.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("rotate_age_key_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        pytest.skip("rotate_age_key.py 不在预期位置")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_secret_key_is_valid_age_bech32():
    module = _load_module()
    _raw, secret, public = module._generate_keypair()

    assert secret.startswith("AGE-SECRET-KEY-")
    assert secret.isupper()
    payload = module._bech32_decode(secret)[1]
    assert len(payload) == 32
    assert module._encode_public(module._public_from_secret(payload)) == public


def test_secret_key_round_trip_is_stable():
    module = _load_module()
    raw, secret, _public = module._generate_keypair()
    decoded = module._bech32_decode(secret)[1]
    reencoded = module._encode_secret(decoded)
    assert reencoded == secret
    assert module._bech32_decode(reencoded)[1] == raw
