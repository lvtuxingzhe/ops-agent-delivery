"""知识库准入常量、规则正则、错误类型与通用文本工具。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .. import knowledge_base_service

DECISIONS = ("admit", "review", "reject")
SOURCE_KINDS = ("deployment", "investigation")
PROMPT_VERSION = 1
MAX_REASON = 300
MAX_SIGNALS = 8
MAX_TAGS = 8

_TEST_RE = re.compile(r"(测试|自检|演练|eval|smoke|demo|回归)", re.IGNORECASE)
_STATUS_QUERY_RE = re.compile(
    r"(查询|查看|检查|看一下|看看|是否正常|状态|负载|CPU|内存|磁盘|端口|进程)",
    re.IGNORECASE,
)
_SYMPTOM_RE = re.compile(
    r"(服务.{0,6}failed|进程.{0,6}不存在|端口.{0,6}(未监听|不通)|"
    r"CPU.{0,6}(偏高|过高|高)|磁盘.{0,8}(90|满)|负载.{0,6}(偏高|高))",
    re.IGNORECASE,
)
_CAUSE_RE = re.compile(r"(因为|由于|导致|根因|原因|配置|依赖|权限|资源不足|冲突|错误)")
_GENERIC_ACTION_RE = re.compile(
    r"^(再查|建议人工|联系负责人|继续观察|进一步排查|确认一下|查看日志)"
)
_HIGH_RISK_RE = re.compile(
    r"(重启|删除|格式化|改配置|修改配置|提权|sudo|停机|下线|卸载|kill|rm -rf)",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"(检查|确认|修改|重启|回滚|验证|启动|停止|备份|更新|调整|排查|查看|清理|重装|切换|卸载|安装)"
)


class CurationError(Exception):
    """准入业务错误；消息可直接展示给管理员。"""


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(value: object, limit: int) -> str:
    text = _clean(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _full_block(value: object) -> str:
    """正文保留换行和步骤，只收敛连续空行；不按字数截断。"""
    return knowledge_base_service.format_knowledge_body(value)


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip()[:60])
    return result[:limit]


def _hash_payload(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
