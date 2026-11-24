"""
工具注册表 —— 显式标记、确定性排序、数量校验。

为什么不按「名字前缀」自动扫描
------------------------------
前缀是命名习惯，不是注册契约：
  1. 名字凑巧的 `query_user_credentials()` 会被扫进来，拿到与取证工具同等的执行权；
  2. 顺序取决于 getmembers 的字母序与导入顺序，多进程下不稳定，而 tools schema
     是请求前缀的一部分，顺序抖一次缓存就抖一次；
  3. 携带不了元数据（数据源、只读）。

现在只有被 `@evidence_tool(source=...)` 人工标记过的方法才会成为工具。
新增数据源 = 写方法 + 打标记，不改这里、不改提示词、不调 toolkit.add()。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentscope.tool import FunctionTool

# 工具数量的合理区间：自动发现是开发便利，不是上线免审。
# 明显跑偏（少一个/多出一堆）说明注册表被改坏了，宁可启动失败。
MIN_EXPECTED_TOOLS = 3
MAX_EXPECTED_TOOLS = 12


class ToolRegistryError(Exception):
    """注册表不合法（重名、数据源未登记、数量越界）。启动期就该失败。"""


@dataclass(frozen=True)
class EvidenceToolSpec:
    """一个取证工具的元数据。name 取方法名，source 必须是已登记的数据源。"""

    name: str
    source: str
    read_only: bool = True


def evidence_tool(source: str, *, read_only: bool = True) -> Callable:
    """
    把方法登记成取证工具。只有被它标记的方法才会被注册。

    source 合法性由调用方（data 层）校验：注册表不 import 数据源，
    避免「注册机制」反过来依赖「数据实现」。
    """

    def decorator(func: Callable) -> Callable:
        """给方法贴上标记；没有标记的方法不会被注册成工具。"""
        func.__evidence_spec__ = EvidenceToolSpec(  # type: ignore[attr-defined]
            name=func.__name__, source=source, read_only=read_only,
        )
        return func

    return decorator


def _spec_of(member: Any) -> EvidenceToolSpec | None:
    """取方法上的标记；绑定方法要把属性从 __func__ 上读。"""
    func = getattr(member, "__func__", member)
    return getattr(func, "__evidence_spec__", None)


def collect_tools(instance: Any, *, allowed_sources: set[str] | None = None,
                  enforce_count: bool = True) -> list[FunctionTool]:
    """
    收集实例上被标记的方法，按 name 排序后包装成工具。

    校验：跨模块方法跳过、重名报错、数据源越界报错、数量越界报错。
    `enforce_count=False` 给「只有一个工具的执行面」用：那里不该套 3～12 的区间。
    """
    owner_module = type(instance).__module__
    specs: dict[str, tuple[EvidenceToolSpec, Any]] = {}

    for _, member in inspect.getmembers(instance, predicate=inspect.ismethod):
        spec = _spec_of(member)
        if spec is None:
            continue
        # 只认本类自己声明的方法；import 进来的同名函数不参与注册
        if getattr(member, "__func__", member).__module__ != owner_module:
            continue
        if spec.name in specs:
            raise ToolRegistryError(f"工具重名：{spec.name}")
        specs[spec.name] = (spec, member)

    if allowed_sources is not None:
        allowed = set(allowed_sources)
        # 只暴露本次任务真正可用的源：真实任务配了 Prometheus 就不把 ELK 样例
        # 或 K8s 样例塞给模型，避免「现场没有这个源，却查出了样例数据」。
        # allowed_sources 是过滤条件，不是「全量源必须一致」的断言：真实任务只配
        # 一个源时，工具数本来就少于演示注册表。
        specs = {
            name: pair for name, pair in specs.items()
            if pair[0].source in allowed
        }

    if enforce_count and not MIN_EXPECTED_TOOLS <= len(specs) <= MAX_EXPECTED_TOOLS:
        raise ToolRegistryError(
            f"工具数量 {len(specs)} 不在预期区间 "
            f"[{MIN_EXPECTED_TOOLS}, {MAX_EXPECTED_TOOLS}]，注册表可能被改坏了",
        )

    ordered = sorted(specs.values(), key=lambda pair: pair[0].name)
    return [
        FunctionTool(
            func=member,
            name=spec.name,
            description=inspect.getdoc(member) or spec.name,
            is_read_only=spec.read_only,
        )
        for spec, member in ordered
    ]


__all__ = [
    "MAX_EXPECTED_TOOLS", "MIN_EXPECTED_TOOLS", "EvidenceToolSpec",
    "ToolRegistryError", "collect_tools", "evidence_tool",
]
