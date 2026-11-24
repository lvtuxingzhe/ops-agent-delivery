"""云端模型网关配置与结果对象。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .contract import data_quality_text
from .db import connect_app
from .redaction import RedactionRule, default_rules

NAMESPACE = "llm_gateway"
CONFIG_KEY = "default"
REDACTION_NAMESPACE = "llm_redaction"

# 状态词。刻意不设「正常」——那是判定，不是状态。
STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_NOT_ENABLED = "not_enabled"
STATUS_BLOCKED = "blocked"
STATUS_DEGRADED = "degraded"
STATUS_INVALID = "invalid"


class GatewayError(Exception):
    """网关配置/流程出错。消息只带原因码与规则编号，不带任何凭据或原文。"""


@dataclass(frozen=True)
class GatewayConfig:
    """
    网关配置。默认值就是「默认关」的落法，全部可被库里的 JSONB 覆盖。

    no_real_call       True 时真实调用一律拒（对应老常量 FIRST_PHASE_NO_REAL_CALL）
    domain_allowlist   精确主机名白名单；空 = 未配置 = 拒绝
    token_budget       单任务上限，0 = 未配置 = 拒绝
    timeout_seconds    单次超时；总耗时靠 max_retries 保障
    max_retries        最多重试几次（只对超时 / 5xx）
    fallback_to_rules  失败时降级到本地规则引擎，绝不阻塞排查
    """

    no_real_call: bool = True
    domain_allowlist: tuple[str, ...] = ()
    token_budget: int = 0
    timeout_seconds: int = 5
    max_retries: int = 1
    max_evidence_chars: int = 20000
    max_items: int = 50
    max_chars_per_item: int = 400
    fallback_to_rules: bool = True
    note: str = ""


DEFAULT_CONFIG = GatewayConfig()


def _as_bool(value: Any, default: bool) -> bool:
    """把配置里的值稳妥地转成布尔，认不出的用默认值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


def _as_int(value: Any, default: int) -> int:
    """把配置里的值稳妥地转成非负整数，认不出的用默认值。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def load_config(settings: Settings) -> GatewayConfig:
    """从 v3_configs 读网关配置；没配过就用「默认关」的本地默认值。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
            (NAMESPACE, CONFIG_KEY),
        ).fetchone()
    if not row or not isinstance(row[0], dict):
        return DEFAULT_CONFIG
    value = row[0]
    hosts = value.get("domain_allowlist") or []
    if not isinstance(hosts, (list, tuple)):
        hosts = []
    return GatewayConfig(
        no_real_call=_as_bool(value.get("no_real_call"), True),
        domain_allowlist=tuple(str(hostname).strip().lower() for hostname in hosts if str(hostname).strip()),
        token_budget=_as_int(value.get("token_budget"), 0),
        timeout_seconds=_as_int(value.get("timeout_seconds"), 5) or 5,
        max_retries=_as_int(value.get("max_retries"), 1),
        max_evidence_chars=_as_int(value.get("max_evidence_chars"), 20000),
        max_items=_as_int(value.get("max_items"), 50),
        max_chars_per_item=_as_int(value.get("max_chars_per_item"), 400),
        fallback_to_rules=_as_bool(value.get("fallback_to_rules"), True),
        note=str(value.get("note", "")),
    )


def load_rules(settings: Settings) -> list[RedactionRule]:
    """从 v3_configs 读脱敏规则；没配过用四类种子。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT value FROM v3_configs WHERE namespace=%s AND key=%s",
            (REDACTION_NAMESPACE, CONFIG_KEY),
        ).fetchone()
    if not row or not isinstance(row[0], dict):
        return default_rules()
    raw_rules = row[0].get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        return default_rules()
    rules = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        rules.append(RedactionRule(
            code=str(item.get("code", "")),
            detector=str(item.get("detector", "")),
            placeholder=str(item.get("placeholder", "")),
            params=item.get("params") or {},
            priority=_as_int(item.get("priority"), 100),
            enabled=_as_bool(item.get("enabled"), True),
        ))
    return rules or default_rules()


@dataclass(frozen=True)
class CallResult:
    """
    一次调用的明确回执。summary 为 None 就是没内容，绝不用空串冒充成功。
    used_model=False 表示这次没用上云端模型（降级/被拒/缺数据）。
    """

    task_id: str
    config_id: int
    status: str
    summary: str | None = None
    used_model: bool = False
    degraded: bool = False
    reason: str = ""
    detail: str = ""
    model_name: str = ""
    text_sha256: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    redaction_count: int = 0
    evidence_ids: list[str] = field(default_factory=list)

    @property
    def data_quality(self) -> str:
        """只有真正拿到结果才算「有数据」，其余一律「无数据」。"""
        return "ok" if self.status == STATUS_OK else "no_data"

    def status_text(self) -> str:
        """把状态翻成页面上的人话，绝不用「正常」两个字。"""
        if self.status == STATUS_OK:
            return data_quality_text("ok")
        return self.detail or data_quality_text("no_data")

    def to_audit_payload(self) -> dict:
        """
        留痕白名单：只记任务号、模型名、脱敏后文本哈希、token 用量、
        耗时和是否降级。不记原文、回包全文、凭据或映射表。
        """
        return {
            "task_id": self.task_id,
            "config_id": self.config_id,
            "model_name": self.model_name,
            "status": self.status,
            "reason": self.reason,
            "degraded": self.degraded,
            "used_model": self.used_model,
            "text_sha256": self.text_sha256,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "elapsed_ms": self.elapsed_ms,
            "redaction_count": self.redaction_count,
        }

    def to_public(self) -> dict:
        """给前端的回执，字段白名单，同样不含任何凭据或原文。"""
        return {
            "allowed": self.status == STATUS_OK,
            "status": self.status,
            "detail": self.status_text(),
            "reason": self.reason,
            "used_model": self.used_model,
            "degraded": self.degraded,
            "data_quality": self.data_quality,
        }


def no_result(task_id: str, config_id: int, *, status: str, reason: str,
              detail: str, degraded: bool = False,
              model_name: str = "") -> CallResult:
    """所有失败路径统一走这一个构造器，不许各写各的。"""
    return CallResult(
        task_id=task_id, config_id=config_id, status=status, reason=reason,
        detail=detail, degraded=degraded, model_name=model_name,
        summary=None,
    )


def seed_values() -> dict[str, dict]:
    """初始化脚本用：两行整行种子，地址/白名单/预算/规则全是配置数据。"""
    return {
        NAMESPACE: {
            "no_real_call": True,
            "domain_allowlist": [],
            "token_budget": 0,
            "timeout_seconds": 5,
            "max_retries": 1,
            "max_evidence_chars": 20000,
            "max_items": 50,
            "max_chars_per_item": 400,
            "fallback_to_rules": True,
            "note": (
                "出网能力已全开但三道默认关保留；本次只验本机回环假端点，"
                "真实公有云调用需 API Key + 出网审批，未连公网。"
            ),
        },
        REDACTION_NAMESPACE: {
            "rules": [
                {
                    "code": rule.code, "detector": rule.detector,
                    "placeholder": rule.placeholder, "params": rule.params,
                    "priority": rule.priority, "enabled": rule.enabled,
                }
                for rule in default_rules()
            ],
            "note": "四类最小清单；加第五类只在配置里加一条规则，不改代码。",
        },
    }


def json_for_seed(value: dict) -> str:
    """把配置字典序列化成能写进数据库的 JSON 文本。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
