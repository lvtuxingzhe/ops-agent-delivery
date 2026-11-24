"""模板 AI 的提示词与回包收紧。"""

from __future__ import annotations

import re
from typing import Any

from ..deployment_service import DeploymentError
from ..deployment_template_service import validate_payload
from .errors import TemplateAIUnavailableError

SYSTEM_PROMPT = (
    "你是运维部署模板助手。只输出一个严格 JSON 对象，不要 Markdown 代码块，不要解释。"
    "JSON 结构：name、description、platform、os_family、package_manager、"
    "default_host、ttl_minutes、risk、precheck、operations、verify、rollback、post_rollback。"
    "os_family 只能是 debian、rhel、windows、any；package_manager 只能是 apt、dnf、yum、none。"
    "precheck/operations/verify/rollback/post_rollback 都是数组，每项是 "
    "{\"command\": \"一行命令\", \"scope\": \"read|dangerous\"}"
    "；precheck/verify/post_rollback 每项还可带 expect："
    "{\"exit_code\": 0, \"stdout_contains\": \"关键字\", \"stderr_contains\": \"关键字\", "
    "\"output_contains\": \"关键字\"}。"
    "输出流不确定的版本/帮助/包管理器命令用 output_contains，不要猜 stdout 或 stderr。"
    "命令必须是一行一个 argv 命令：禁止管道 |、重定向 > <、&&、;、反引号、$(...)。"
    "apply 至少一条；verify 至少一条且必须能判断成功；rollback 至少一条。"
    "precheck 是变更前只读检查，用于记录变更前状态；可以省略。"
    "verify 只验证变更后的状态，禁止把变更前检查放进 verify。"
    "post_rollback 是回滚后的只读验证，只在 rollback 执行后运行；可以省略。"
    "只读检查用 read，重启/停止/删除/改配置/提权用 dangerous。"
    "包管理器必须匹配目标机：os_family=debian 只能用 apt/apt-get；"
    "os_family=rhel 只能用 dnf/yum；windows 不用 Linux 包管理命令。"
    "没有包管理动作时 os_family 用 any、package_manager 用 none。"
    "如果不知道目标机发行版，不要猜 apt 或 dnf；返回 needs_os_info 并说明缺什么信息。"
    "不要编造主机上不存在的服务名、文件路径或端口；信息不足时把命令留空并说明原因。"
    "不要输出密码、Token、私钥或任何凭据；不要写 curl | bash 这类下载执行命令。"
    "平台只允许 linux 或 windows；返回内容必须能被下面的结构校验接受。"
)

_FORBIDDEN_COMMAND = re.compile(r"[|><;&`]|\$\(|\$\{")


def _normalize_ai_reply(
    reply: dict[str, Any], *, goal: str, platform: str, host: str,
) -> dict[str, Any]:
    """把模型 JSON 收紧成模板草稿；结构校验失败就抛，不降级成空模板。"""
    if not isinstance(reply, dict):
        raise TemplateAIUnavailableError("invalid_response：模型没有返回 JSON 对象")
    name = str(reply.get("name") or "").strip()
    if not name:
        name = (goal.strip().splitlines()[0] if goal.strip() else "AI 部署草稿")[:80]
    payload = {
        "name": name[:80],
        "description": str(reply.get("description") or goal)[:2000],
        "platform": str(reply.get("platform") or platform).strip().lower(),
        "os_family": str(reply.get("os_family") or "").strip().lower(),
        "package_manager": str(reply.get("package_manager") or "").strip().lower(),
        "default_host": str(reply.get("default_host") or host or "local").strip()[:64],
        "ttl_minutes": reply.get("ttl_minutes") or 30,
        "risk": str(reply.get("risk") or "").strip().lower(),
        "precheck": reply.get("precheck") or [],
        "operations": reply.get("operations"),
        "verify": reply.get("verify"),
        "rollback": reply.get("rollback"),
        "post_rollback": reply.get("post_rollback") or [],
    }
    if payload["platform"] not in ("linux", "windows"):
        raise TemplateAIUnavailableError("invalid_response：platform 只能是 linux 或 windows")
    for key in ("operations", "verify", "rollback", "precheck", "post_rollback"):
        items = payload[key]
        if key in ("precheck", "post_rollback") and not items:
            continue
        if not isinstance(items, list) or not items:
            raise TemplateAIUnavailableError(f"invalid_response：缺少 {key}")
        for item in items:
            if not isinstance(item, dict):
                raise TemplateAIUnavailableError(f"invalid_response：{key} 里有非对象项")
            command = str(item.get("command") or "").strip()
            if not command:
                raise TemplateAIUnavailableError(f"invalid_response：{key} 里有空命令")
            if _FORBIDDEN_COMMAND.search(command):
                raise TemplateAIUnavailableError(
                    f"invalid_response：{key} 命令包含 shell 元字符，执行器不支持"
                )
    try:
        return validate_payload(payload)
    except DeploymentError as exc:
        raise TemplateAIUnavailableError(f"invalid_draft：{exc}") from exc
