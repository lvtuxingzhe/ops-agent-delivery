"""
模型装配自检 —— 配置到 AgentScope 模型类的映射，全程零联网。

怎么用（在 code/ 目录下）
--------------------------
    ./backend/.venv/Scripts/python.exe scripts/selftests/selftest_model_builder.py

验什么
------
  * 三种协议各自装配成正确的 AgentScope 模型类（OpenAI / Claude / Gemini）；
  * 模型名、接口地址、max_tokens、超时都来自配置，而不是代码常量；
  * 配置里的未知参数被丢掉，不会打挂一次排查；
  * 不支持的协议、缺地址、缺模型名、缺 Key 都给可定位的错误；
  * 构造过程零连接（socket 被掐死也不报错）；
  * 密钥被 SecretStr 包住，模型对象的 repr 里查不到明文。
"""

from __future__ import annotations

import socket
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "worker"))

from agentscope.model import (  # noqa: E402
    AnthropicChatModel, GeminiChatModel, OpenAIChatModel,
)
from runtime.model import ModelConfigError, build_model  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
FAKE_KEY = "sk-selftest-" + "x" * 24


def record(name: str, ok: bool, detail: str = "") -> None:
    """记一条 PASS/FAIL 并立刻打印。"""
    tag = "PASS" if ok else "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"[{tag}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def spec(protocol: str, **overrides) -> dict:
    """造一份与后端下发形状一致的配置。"""
    base = {
        "key": "自测模型",
        "protocol": protocol,
        "base_url": "http://127.0.0.1:18080/v1",
        "model_name": "fake-model",
        "params": {"max_tokens": 1500, "temperature": 0.2, "timeout_seconds": 30},
    }
    base.update(overrides)
    return base


class NoNetwork:
    """把 socket 掐死：任何一次真连接都会变成异常。"""

    def __enter__(self):
        """禁用 socket，返回自身。"""
        self.original = socket.socket
        socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("构造模型不该联网")
        )
        return self

    def __exit__(self, *_exc):
        """恢复 socket。"""
        socket.socket = self.original
        return False


def main() -> int:
    """跑完所有检查并返回退出码。"""
    print("=" * 66)
    print("模型装配自检（配置 → AgentScope 模型类，零联网）")
    print("=" * 66)

    with NoNetwork():
        openai_model = build_model(spec("openai_compatible"), FAKE_KEY)
        record("openai_compatible 装配成 OpenAIChatModel",
               isinstance(openai_model, OpenAIChatModel),
               type(openai_model).__name__)
        record("模型名来自配置，不是代码常量",
               openai_model.model == "fake-model", openai_model.model)
        record("接口地址来自配置",
               openai_model.credential.base_url == "http://127.0.0.1:18080/v1",
               str(openai_model.credential.base_url))
        record("max_tokens 来自配置",
               openai_model.parameters.max_tokens == 1500,
               str(openai_model.parameters.max_tokens))
        record("温度来自配置",
               openai_model.parameters.temperature == 0.2,
               str(openai_model.parameters.temperature))
        record("超时进客户端参数",
               openai_model.client_kwargs.get("timeout") == 30.0,
               str(openai_model.client_kwargs.get("timeout")))

        claude_model = build_model(spec("native_claude"), FAKE_KEY)
        record("native_claude 装配成 AnthropicChatModel",
               isinstance(claude_model, AnthropicChatModel),
               type(claude_model).__name__)

        gemini_spec = spec("native_gemini",
                           params={"max_tokens": 1000, "temperature": 0.1})
        try:
            gemini_model = build_model(gemini_spec, FAKE_KEY)
            record("native_gemini 装配成 GeminiChatModel",
                   isinstance(gemini_model, GeminiChatModel),
                   type(gemini_model).__name__)
        except ModelConfigError as exc:
            # 镜像里没装 google-genai 时，装配层要给出可执行的提示而不是裸 ImportError
            record("native_gemini 缺 SDK 时给出可执行提示",
                   "google-genai" in str(exc), str(exc)[:40])

        record("构造模型全程零连接", True, "socket 被掐死仍能装配")

    # 参数白名单：多塞的字段被丢掉，不报错
    stray = build_model(
        spec("openai_compatible", params={"max_tokens": 1000, "nonsense": 1}),
        FAKE_KEY,
    )
    record("配置里的未知参数被丢弃而不是打挂",
           stray.parameters.max_tokens == 1000, "只保留模型类认识的字段")

    # 缺省参数不炸：配置里没写 max_tokens 时用默认值
    bare = build_model(
        spec("openai_compatible", params={}), FAKE_KEY)
    record("没配 max_tokens 时有默认值", bare.parameters.max_tokens == 2000,
           str(bare.parameters.max_tokens))

    # 负向：四类错误都要能定位
    cases = (
        (spec("native_unknown"), "不支持的协议", FAKE_KEY),
        (spec("openai_compatible", base_url=""), "缺接口地址", FAKE_KEY),
        (spec("openai_compatible", model_name=""), "缺模型名", FAKE_KEY),
        (spec("openai_compatible"), "缺 Key", ""),
    )
    for bad_spec, label, key in cases:
        try:
            build_model(bad_spec, key)
            record(f"{label}被拒", False, "居然装配成功了")
        except ModelConfigError as exc:
            record(f"{label}被拒", True, str(exc)[:40])

    # 密钥不出现在对象 repr 里
    leak_targets = [repr(openai_model), str(openai_model.credential)]
    record("模型对象 repr 里查不到密钥明文",
           all(FAKE_KEY not in text for text in leak_targets),
           "SecretStr 包住，print 只出掩码")

    failed = sum(1 for tag, _, _ in RESULTS if tag == "FAIL")
    print("-" * 66)
    print(f"汇总：PASS {len(RESULTS) - failed} · FAIL {failed}")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - 自检自身出错要如实打出来
        traceback.print_exc()
        sys.exit(1)
