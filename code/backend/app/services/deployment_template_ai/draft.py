"""模板 AI 草稿生成：复用既有出网、脱敏与模型闸口。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ...core.config import Settings
from ...core.credentials import STATUS_OK as CRED_OK
from ...core.credentials import resolve as resolve_credential
from ...core.llm_gateway import load_config as load_gateway_config
from ...core.llm_gateway import load_rules
from ...core.llm_json import extract_json
from ...core.llm_transport import (
    DomainNotAllowedError,
    TransportError,
    chat_completions_url,
    check_target,
    send,
)
from ...core.redaction import RedactionError, RedactionSession, assert_no_leak, redact, restore
from .. import llm_config_service as llm
from .context import _host_facts
from .errors import TemplateAIUnavailableError
from .prompt import SYSTEM_PROMPT, _normalize_ai_reply


def generate_draft(
    settings: Settings, *, actor_id: int, goal: str, platform: str,
    host: str = "local", pasted_commands: str = "",
    client_source: str = "deployment",
) -> dict[str, Any]:
    """返回 {draft, ai}；本函数不写入数据库，由 API 层统一创建 draft。"""
    goal = (goal or "").strip()
    if not goal or len(goal) > 2000:
        raise TemplateAIUnavailableError("goal_invalid：请用 1～2000 字描述要部署什么")
    platform = (platform or "").strip().lower()
    if platform not in ("linux", "windows"):
        raise TemplateAIUnavailableError("platform_invalid：只支持 linux 或 windows")
    host = (host or "local").strip()[:64]
    if len(pasted_commands) > 2000:
        raise TemplateAIUnavailableError("commands_too_long：命令原文最多 2000 字")
    host_facts = _host_facts(settings, host, platform)
    if platform == "linux" and host_facts["os_family"] == "unknown":
        raise TemplateAIUnavailableError(
            "needs_os_info：目标主机未登记操作系统，请先在主机页填写 os_family 和 package_manager"
        )

    config = llm.get_default_config(settings)
    if not config:
        raise TemplateAIUnavailableError("no_default_model：没有启用并设为默认的模型")
    gate = llm.assert_call_allowed(settings, config["key"], actor_id, client_source)
    if not gate.get("allowed"):
        raise TemplateAIUnavailableError(
            f"{gate.get('reason') or 'gate_blocked'}：{gate.get('message') or '模型闸口未放行'}"
        )
    cred = resolve_credential(config.get("credential_ref", ""), settings, actor_kind="investigation")
    if cred.status != CRED_OK or cred.value is None:
        raise TemplateAIUnavailableError("credential_no_data：模型凭据未接入")
    gateway = load_gateway_config(settings)
    if gateway.token_budget <= 0:
        raise TemplateAIUnavailableError("budget_exceeded：模型 token 预算未配置")

    request_text = json.dumps({
        "goal": goal,
        "platform": platform,
        "host": host,
        "host_facts": host_facts,
        "pasted_commands": pasted_commands,
        "output_language": "中文（命令本身按目标平台）",
    }, ensure_ascii=False)
    if len(request_text) > gateway.max_evidence_chars:
        request_text = request_text[: gateway.max_evidence_chars] + " [已截断]"

    trace: list[dict[str, Any]] = [
        {
            "index": 1, "kind": "step", "title": "读取目标主机事实", "status": "ok",
            "detail": (
                f"{host_facts['id']} · {host_facts['platform']} · "
                f"{host_facts['os_family']}/{host_facts['distro']} "
                f"{host_facts['os_version']} · 包管理器 {host_facts['package_manager']}"
            ),
        },
        {
            "index": 2, "kind": "step", "title": "检查模型闸口", "status": "ok",
            "detail": f"模型 {config.get('key', '')} · {config.get('model_name', '')} · 默认温度 0",
        },
    ]

    session = RedactionSession(task_id=f"ai-deploy-template-{actor_id}-{datetime.now(UTC).timestamp()}")
    try:
        red = redact(request_text, load_rules(settings), session,
                     task_id=f"ai-deploy-template-{actor_id}")
        payload = {
            "model": config.get("model_name", ""),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": red.text},
            ],
            "temperature": 0,
            "max_tokens": min(gateway.token_budget, 1600),
        }
        assert_no_leak(payload, session)
        check_target(chat_completions_url(config.get("base_url", "")), gateway.domain_allowlist)
    except (RedactionError, DomainNotAllowedError) as exc:
        raise TemplateAIUnavailableError(str(exc).split(":")[0] or "redaction_failed") from exc

    raw_content = ""
    try:
        status, reply, _elapsed = send(
            url=chat_completions_url(config.get("base_url", "")),
            model_name=config.get("model_name", ""),
            redacted_payload=payload, session=session, secret=cred.value,
            allowlist=gateway.domain_allowlist, timeout_seconds=gateway.timeout_seconds,
        )
        if not (200 <= status < 300):
            raise TemplateAIUnavailableError(f"upstream_http_{status}")
        choices = reply.get("choices")
        if not isinstance(choices, list) or not choices:
            raise TemplateAIUnavailableError("invalid_response")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        raw_content = str((message or {}).get("content", "") or "")
        reasoning = str(
            (message or {}).get("reasoning_content")
            or (message or {}).get("reasoning")
            or ""
        ).strip()
        trace.append({
            "index": 3, "kind": "step", "title": "调用模型生成草稿", "status": "ok",
            "detail": f"模型返回 {len(raw_content)} 字；显式推理 {len(reasoning)} 字",
        })
        if reasoning:
            trace.append({
                "index": 4, "kind": "thinking", "title": "模型显式推理",
                "detail": reasoning[:2000],
            })
    except TemplateAIUnavailableError:
        raise
    except TransportError as exc:
        raise TemplateAIUnavailableError(str(exc).split(":")[0] or "network_error") from exc

    restored = restore(raw_content, session)
    if restored is None:
        raise TemplateAIUnavailableError("invalid_response：回包反替换失败")
    try:
        parsed = extract_json(restored)
    except (ValueError, json.JSONDecodeError) as exc:
        raise TemplateAIUnavailableError("invalid_response：不是合法 JSON") from exc
    draft = _normalize_ai_reply(parsed, goal=goal, platform=platform, host=host)
    trace.append({
        "index": len(trace) + 1, "kind": "step", "title": "解析 JSON 与结构校验",
        "status": "ok",
        "detail": (
            f"apply {len(draft['template']['operations'])} 条 / "
            f"verify {len(draft['template']['verify'])} 条 / "
            f"rollback {len(draft['template']['rollback'])} 条；"
            f"风险 {draft['risk']}"
        ),
    })
    template_os = str(draft["template"].get("os_family") or "unknown")
    target_os = host_facts["os_family"]
    if target_os in ("debian", "rhel", "windows") and template_os not in ("any", target_os):
        raise TemplateAIUnavailableError(
            f"需要 target_os={target_os}，但模型生成了 os_family={template_os} 的模板"
        )
    trace.append({
        "index": len(trace) + 1, "kind": "step", "title": "目标机兼容校验",
        "status": "ok",
        "detail": f"模板 os_family={template_os} · 目标机 os_family={target_os}",
    })
    ai_meta = {
        "model": config.get("key", ""),
        "model_name": config.get("model_name", ""),
        "prompt_sha256": hashlib.sha256(restored.encode("utf-8")).hexdigest(),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return {"template": draft["template"], "ai": ai_meta,
            "warnings": draft.get("warnings", []), "risk": draft["risk"],
            "trace": trace[:20]}
