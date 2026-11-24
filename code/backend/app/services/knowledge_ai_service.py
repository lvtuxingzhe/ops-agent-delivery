"""知识库 AI 能力 —— 上传摘要 + 入库准入，复用平台模型闸口与脱敏链路。

定位
----
知识库上传接口在文件落盘、抽取正文之后调用这里，为文档生成一句 50 字以内的
中文摘要；知识准入队列在规则预筛通过后调用 ``judge_curation``，让模型判断
“这条部署经验/排查报告值不值得入库”。

三条边界
--------
1. 默认开、可关：配置页 ``knowledge_ai_summary_enabled`` /
   ``knowledge_ai_curation_enabled`` 关闭后直接返回确定性结果或转人工复核。
2. 不可用就降级：没有默认模型、闸口未放行、凭据没接入、预算未配置、网络失败、
   回包不是文本，全部走降级路径，不阻断上传或队列 worker。
3. 送外部模型的内容有上限：摘要只送前 3000 字，准入候选只送前 12000 字；
   送之前走脱敏，回包恢复占位符，避免把凭证/内网地址带出去。
"""

from __future__ import annotations

import json
import logging
import re

from ..core.config import Settings
from ..core.credentials import STATUS_OK as CRED_OK
from ..core.credentials import resolve as resolve_credential
from ..core.db import connect_app
from ..core.llm_gateway import load_config as load_gateway_config
from ..core.llm_gateway import load_rules
from ..core.llm_json import JsonExtractError, extract_json
from ..core.llm_transport import (
    DomainNotAllowedError,
    chat_completions_url,
    check_target,
    send,
)
from ..core.redaction import (
    RedactionError,
    RedactionSession,
    assert_no_leak,
    redact,
    restore,
)
from .config_service import get_setting
from .knowledge_file_service import MAX_SUMMARY, fallback_summary
from .llm_config_service import assert_call_allowed, get_default_config

log = logging.getLogger(__name__)

# 只把文档开头 3000 字送去外部模型；用户已确认这个上限。
MAX_AI_INPUT_CHARS = 3_000
# 准入候选包含报告、假设、方案和证据摘要，给到 12000 字足够判断，
# 也避免把整份长报告原样送出。
MAX_CURATION_INPUT_CHARS = 12_000

_SUMMARY_PROMPT = (
    "你是运维知识库编辑。请为上传的文档写一句不超过 50 个汉字的中文摘要，"
    "说明这篇文档讲什么、适合什么场景使用，帮助一线工程师判断要不要打开它。"
    "只输出摘要正文，不要标题、引号、Markdown、序号或“摘要：”前缀。"
)

_CURATION_PROMPT = (
    "你是运维知识库准入编辑。只根据给出的任务事实判断这条经验值不值得进知识库，"
    "不要补造命令、主机、时间、根因；正文不要压缩、不要截断。输出严格 JSON，字段固定：\n"
    '{"decision":"admit|review|reject","value_score":0-10,'
    '"reason":"一句话理由","signals":["..."],'
    '"curated":{"title":"不超过60字","summary":"不超过50字",'
    '"body":"留空；完整正文由系统保留","tags":["..."],"tech_domain":"枚举值"}}\n'
    "判定规则：只有现象、没有根因判断，或没有具体处理步骤，一律 reject；"
    "根因和方案都有但证据弱、与已有条目高度相似、方案含高风险动作时用 review；"
    "只有证据支撑、可跨主机复用、方案具体且低风险的才 admit。"
    "tech_domain 只能是 middleware/database/kubernetes/docker/dependency/"
    "network/security/monitor/other 之一。signals 只写短标签。"
)


def _clean_summary(raw: str) -> str:
    """模型回包可能带引号/标题/前缀，整理成一句 50 字以内的话。"""
    text = raw.replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"^(摘要|总结|一句话摘要)\s*[:：]\s*", "", text)
    text = text.strip("`'\"“”‘’ ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_SUMMARY:
        text = text[: MAX_SUMMARY - 1] + "…"
    return text


def _fallback(title: str, text: str) -> dict:
    return {
        "summary": fallback_summary(title, text),
        "source": "fallback",
        "model": "",
    }


def _ai_setup(settings: Settings, actor_id: int, client_source: str) -> dict | None:
    """复用同一套“默认模型 + 闸口 + 凭据 + 网关”前置检查。"""
    config = get_default_config(settings)
    if not config:
        return None
    try:
        gate = assert_call_allowed(settings, config["key"], actor_id, client_source)
        if not gate.get("allowed"):
            return None
        cred = resolve_credential(
            config.get("credential_ref", ""), settings, actor_kind="investigation"
        )
        if cred.status != CRED_OK or cred.value is None:
            return None
        gateway = load_gateway_config(settings)
        if gateway.token_budget <= 0:
            return None
    except Exception:  # noqa: BLE001 - 闸口/凭据/预算任一不可用都降级
        return None
    return {"config": config, "cred": cred, "gateway": gateway}


def _call_model(settings: Settings, setup: dict, *, system: str,
                evidence: str, task_id: str, max_tokens: int) -> str | None:
    """脱敏 → 出网 → 还原；任何一步失败都返回 None，不向外抛异常。"""
    config = setup["config"]
    cred = setup["cred"]
    gateway = setup["gateway"]
    session = RedactionSession(task_id=task_id)
    try:
        red = redact(evidence, load_rules(settings), session, task_id=task_id)
        payload = {
            "model": config.get("model_name", ""),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": red.text},
            ],
            "temperature": 0,
            "max_tokens": min(gateway.token_budget, max_tokens),
        }
        assert_no_leak(payload, session)
        check_target(
            chat_completions_url(config.get("base_url", "")),
            gateway.domain_allowlist,
        )
    except (RedactionError, DomainNotAllowedError):
        return None

    try:
        status, reply, _elapsed = send(
            url=chat_completions_url(config.get("base_url", "")),
            model_name=config.get("model_name", ""),
            redacted_payload=payload,
            session=session,
            secret=cred.value,
            allowlist=gateway.domain_allowlist,
            timeout_seconds=gateway.timeout_seconds,
        )
        if not 200 <= status < 300:
            return None
        choices = reply.get("choices") if isinstance(reply, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = str((message or {}).get("content", "") or "")
    except Exception:  # noqa: BLE001 - 回包异常一律降级
        return None
    return restore(content, session)


def summarize_document(
    settings: Settings,
    *,
    title: str,
    text: str,
    file_type: str,
    actor_id: int = 0,
    client_source: str = "knowledge_upload",
) -> dict:
    """返回 ``summary`` / ``source`` / ``model``；任何异常都不向外抛。"""
    if not text.strip():
        return _fallback(title, text)

    try:
        with connect_app(settings) as conn:
            if get_setting(conn, "knowledge_ai_summary_enabled") != "true":
                return _fallback(title, text)
    except Exception:  # noqa: BLE001 - 读不到开关就按“关”处理，上传不受影响
        return _fallback(title, text)

    setup = _ai_setup(settings, actor_id, client_source)
    if setup is None:
        return _fallback(title, text)

    evidence = json.dumps(
        {
            "title": title,
            "file_type": file_type,
            "content_excerpt": text[:MAX_AI_INPUT_CHARS],
        },
        ensure_ascii=False,
    )
    content = _call_model(
        settings, setup, system=_SUMMARY_PROMPT, evidence=evidence,
        task_id=f"kb-summary-{file_type}", max_tokens=120,
    )
    summary = _clean_summary(content or "")
    if not summary:
        return _fallback(title, text)
    return {
        "summary": summary,
        "source": "ai",
        "model": str(setup["config"].get("model_name") or setup["config"].get("key") or ""),
    }


def judge_curation(
    settings: Settings,
    *,
    candidate: dict,
    actor_id: int = 0,
    client_source: str = "knowledge_curation",
) -> dict:
    """让模型判一次知识价值；返回 ``available`` / ``model`` / ``data``。

    ``available=False`` 表示模型不可用，调用方必须转人工复核，不能自己补一个
    admit。``data`` 是模型原始 JSON，枚举归一由准入服务负责。
    """
    try:
        with connect_app(settings) as conn:
            if get_setting(conn, "knowledge_ai_curation_enabled") != "true":
                return {"available": False, "reason": "curation_disabled", "model": "", "data": {}}
    except Exception:  # noqa: BLE001 - 读不到开关按关闭处理
        return {"available": False, "reason": "curation_disabled", "model": "", "data": {}}

    setup = _ai_setup(settings, actor_id, client_source)
    if setup is None:
        return {"available": False, "reason": "model_unavailable", "model": "", "data": {}}

    evidence = json.dumps(candidate, ensure_ascii=False)[:MAX_CURATION_INPUT_CHARS]
    content = _call_model(
        settings, setup, system=_CURATION_PROMPT, evidence=evidence,
        task_id=f"kb-curation-{candidate.get('task_id', '')}", max_tokens=1800,
    )
    if not content:
        return {"available": False, "reason": "model_unavailable", "model": "", "data": {}}
    try:
        data = extract_json(content)
    except JsonExtractError:
        return {"available": False, "reason": "invalid_response", "model": "", "data": {}}
    return {
        "available": True,
        "reason": "",
        "model": str(setup["config"].get("model_name") or setup["config"].get("key") or ""),
        "data": data,
    }
