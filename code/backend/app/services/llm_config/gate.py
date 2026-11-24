"""模型调用的三道闸口与审计。"""

from __future__ import annotations

import uuid

from ...core.audit import AuditEvent, insert_audit_event
from ...core.config import Settings
from ...core.db import connect_app
from ...core.llm_gateway import load_config as load_gateway_config
from ..config_service import get_setting
from .core import FIRST_PHASE_NO_REAL_CALL, _select_config


def assert_call_allowed(
    settings: Settings, config_key: str, actor_id: int,
    client_source: str = "api",
) -> dict:
    """
    页面「测试连通」按钮调这里，也是 call_model() 的第一道预检。

    第 ④ 步起【能力已全开但默认全关】，检查顺序（三道是「与」）：
      1. 这条配置自己 enabled（默认关）→ config_disabled
      2. 平台总开关 llm_enabled_by_task（默认关）→ task_switch_off
      3. 网关 no_real_call（默认 true）→ first_phase_no_outbound
    三道全过才返回 allowed=True，由 api 层委托真实调用链。
    **reason 字符串一字未改**，所以 ④ 扩展 34/0 的 6a/6b/6c 在默认态
    仍逐条 PASS；放行只需把配置 llm_gateway/no_real_call 改成 false，
    不改这一行代码（「改配置不用改代码」）。

    返回值是明确回执，不抛异常。这里【不发任何网络请求、不读密钥】。
    """
    with connect_app(settings) as conn:
        row = _select_config(conn, config_key)
        task_switch = get_setting(conn, "llm_enabled_by_task") == "true"

    # 第三道读配置；常量 FIRST_PHASE_NO_REAL_CALL 是它的默认值来源。
    # load_gateway_config 只连本机数据库读 v3_configs，不产生外部连接。
    try:
        gate_config = load_gateway_config(settings)
        no_real_call = gate_config.no_real_call
    except Exception:  # noqa: BLE001 - 读不到配置时按最保守的「拒」处理
        no_real_call = FIRST_PHASE_NO_REAL_CALL

    if not row:
        reason = "config_not_found"
    elif not bool(row[3]):
        # row = (key, value, credential_ref, enabled, updated_at)
        reason = "config_disabled"
    elif not task_switch:
        reason = "task_switch_off"
    elif no_real_call:
        reason = "first_phase_no_outbound"
    else:
        reason = ""

    # 放行与拒绝都留痕：拒绝沿用旧事件类型 llm.call.blocked（34/0 可查），
    # 放行新增 llm.call.allowed，payload 只有 config_id，不含任何凭据。
    with connect_app(settings) as conn:
        with conn.transaction():
            insert_audit_event(
                conn,
                AuditEvent(
                    trace_id=f"llm-call-{uuid.uuid4()}",
                    event_type="llm.call.blocked" if reason else "llm.call.allowed",
                    actor=str(actor_id),
                    client_source=client_source,
                    payload={"config_id": config_key, "reason": reason or "allowed"},
                ),
            )

    if not reason:
        return {"allowed": True, "reason": "allowed", "message": "调用已放行"}

    messages = {
        "config_not_found": "没有这条模型配置",
        "config_disabled": "这条模型配置已停用，请先启用",
        "task_switch_off": "平台「按任务开」总开关未打开",
        "first_phase_no_outbound": "当前版本未开通出网，真实模型调用一律拒绝（已留痕）",
    }
    return {"allowed": False, "reason": reason, "message": messages[reason]}
