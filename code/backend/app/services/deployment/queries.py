"""部署任务详情、列表与历史结果兼容。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ...core.config import Settings
from ...core.db import connect_app
from .. import deployment_knowledge_service, task_access
from ..deployment_taxonomy import describe_deploy_type, normalize_deploy_type
from ..verification.expect import expect_ok
from .model import DeploymentError, _normalize_reason_code, deploy_type_of


def _deployment_failure(
    plan: dict, exec_part: dict, *, outcome: str | None, reason_code: str, stage: str | None
) -> dict | None:
    """把部署失败收敛成前端可直接展示的结构化原因。"""
    exec_failure = exec_part.get("failure") if isinstance(exec_part, dict) else None
    if isinstance(exec_failure, dict) and exec_failure:
        return exec_failure
    plan = plan if isinstance(plan, dict) else {}
    ai_error = str(plan.get("ai_error") or "").strip()
    if plan.get("ai_status") == "failed" or reason_code.startswith("ai_plan_failed"):
        message = ai_error or reason_code or "AI 规划失败"
        reason = ""
        for separator in ("：", ":"):
            if separator in message:
                reason = message.split(separator, 1)[0].strip()
                break
        return {
            "stage": "ai_plan",
            "code": "ai_plan_failed",
            "reason": reason,
            "message": message,
        }
    if outcome == "failed":
        code = reason_code or "deployment_failed"
        return {
            "stage": stage or "",
            "code": code,
            "message": reason_code or "部署执行失败",
        }
    return None


_STAGE_RESULT_KEYS = {
    "precheck": "precheck_results",
    "apply": "results",
    "verify": "verify_results",
    "rollback": "rollback_results",
    "post_rollback": "post_rollback_results",
}


def _legacy_expect_ok(op: dict, result: dict) -> bool:
    """历史结果没有 expect_ok 时，复用统一规则重新计算业务是否满足。"""
    return expect_ok(op, result)


def _hydrate_legacy_expect_results(exec_part: dict, failure: dict | None, plan: dict | None = None) -> None:
    """历史任务结果没有 `expect_ok`，读时补上，避免旧记录把失败命令画成通过。

    新任务的每条结果已经带 `expect_ok`，这里只在缺失时回填：
    - 有方案时按方案的 expect 重新计算（能正确处理“期望退出码 1”）；
    - 没方案时默认用命令的 `ok`；
    - 再用结构化 `failure.stage/command_index` 把真正不满足预期的那条改成 False；
    - 回滚失败命令用 `failure.rollback_failed_commands` 里的索引一并纠正。
    """
    stage_ops = {
        "precheck": (plan or {}).get("precheck") or [],
        "apply": (plan or {}).get("operations") or [],
        "verify": (plan or {}).get("verify") or [],
        "rollback": (plan or {}).get("rollback") or [],
        "post_rollback": (plan or {}).get("post_rollback") or [],
    }
    for stage, key in _STAGE_RESULT_KEYS.items():
        items = exec_part.get(key)
        if not isinstance(items, list):
            continue
        ops = stage_ops.get(stage) or []
        for index, item in enumerate(items):
            if isinstance(item, dict) and "expect_ok" not in item:
                if index < len(ops) and isinstance(ops[index], dict):
                    item["expect_ok"] = _legacy_expect_ok(ops[index], item)
                else:
                    item["expect_ok"] = bool(item.get("ok"))
    if not isinstance(failure, dict):
        return
    stage = str(failure.get("stage") or "")
    result_key = _STAGE_RESULT_KEYS.get(stage)
    command_index = failure.get("command_index")
    items = exec_part.get(result_key) if result_key else None
    if (
        isinstance(items, list)
        and isinstance(command_index, int)
        and 0 <= command_index < len(items)
        and isinstance(items[command_index], dict)
    ):
        code = str(failure.get("code") or "")
        if "expect_mismatch" in code or code.endswith("_failed"):
            items[command_index]["expect_ok"] = False
    for failed in failure.get("rollback_failed_commands") or []:
        if not isinstance(failed, dict):
            continue
        rollback_index = failed.get("command_index")
        rollback_items = exec_part.get("rollback_results")
        if (
            isinstance(rollback_items, list)
            and isinstance(rollback_index, int)
            and 0 <= rollback_index < len(rollback_items)
            and isinstance(rollback_items[rollback_index], dict)
        ):
            rollback_items[rollback_index]["expect_ok"] = False


def redact_exec_part(exec_part: dict) -> dict:
    """返回给页面前剥离执行许可与主机密文。

    `permits` 是一次性执行令牌，`target.password_enc` 是主机密码密文；
    两者都只应留在 worker 读取的任务行里，不能出现在部署详情响应。
    """
    if not isinstance(exec_part, dict):
        return {}
    cleaned = {key: value for key, value in exec_part.items()
               if key not in ("permits", "permit_items")}
    target = cleaned.get("target")
    if isinstance(target, dict):
        cleaned["target"] = {key: value for key, value in target.items()
                             if key != "password_enc"}
    return cleaned


def get_deployment(settings: Settings, task_id: str, user: dict) -> dict | None:
    """任务详情：plan 全量给前端；approval 里的 permits 令牌一律剔除。"""
    if not task_access.load_visible_task(settings, task_id, user):
        return None
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            SELECT t.id, t.trace_id, t.task_type, t.status, t.outcome, t.reason_code, t.stage,
                   t.title, t.description, t.target_scope, t.submitted_by, t.confirmed_by,
                   t.plan, t.approval, t.error, t.created_at, t.updated_at, t.closed_at,
                   t.deploy_type, su.username, su.display_name, cu.username, cu.display_name,
                   COALESCE(t.approval->'exec'->>'issued_at',
                            t.approval->'confirmation'->>'approved_at',
                            t.approval->'confirmation'->>'rejected_at') AS approved_at,
                   COALESCE(t.approval->'exec'->>'approval_reason',
                            t.approval->'confirmation'->>'reason') AS approval_reason
              FROM v3_tasks t
              LEFT JOIN v3_users su ON su.id = t.submitted_by
              LEFT JOIN v3_users cu ON cu.id = t.confirmed_by
             WHERE t.id=%s
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return None
    approval = row[13] if isinstance(row[13], dict) else {}
    exec_part = redact_exec_part(approval.get("exec") if isinstance(approval.get("exec"), dict) else {})
    approval = {**approval, "exec": exec_part}
    plan = row[12] or {}
    reason_code = _normalize_reason_code(row[5])
    failure = _deployment_failure(
        plan,
        exec_part,
        outcome=row[4],
        reason_code=reason_code,
        stage=row[6],
    )
    _hydrate_legacy_expect_results(exec_part, failure, plan)
    try:
        knowledge = deployment_knowledge_service.search_similar(
            settings,
            query=row[7] or "",
            template_id=str(plan.get("template_id") or ""),
            os_family=str(plan.get("host_os_family") or plan.get("os_family") or ""),
            reason_code=reason_code,
            stage=str(row[6] or ""),
            host=str(plan.get("host") or ""),
            exclude_task_id=str(row[0]),
            viewers=[user],
            viewer=user,
        )
    except Exception:  # noqa: BLE001 - 知识检索失败不能拖垮部署详情
        knowledge = []
    return {
        "id": str(row[0]),
        "trace_id": row[1],
        "task_type": row[2],
        "status": row[3],
        "outcome": row[4],
        "reason_code": reason_code,
        "stage": row[6],
        "title": row[7],
        "description": row[8],
        "target_scope": row[9] or {},
        "submitted_by": row[10],
        "submitted_by_name": row[20] or row[19] or "",
        "confirmed_by": row[11],
        "confirmed_by_name": row[22] or row[21] or "",
        "approved_at": row[23],
        "approval_reason": row[24] or "",
        "plan": plan,
        "plan_hash": plan.get("plan_hash", ""),
        "approval": approval,
        "error": row[14] or {},
        "failure": failure,
        "deployment_knowledge": knowledge,
        "deploy_type": deploy_type_of(row[7], plan, row[18]),
        "deploy_type_label": describe_deploy_type(deploy_type_of(row[7], plan, row[18])),
        "created_at": row[15].isoformat() if row[15] else None,
        "updated_at": row[16].isoformat() if row[16] else None,
        "closed_at": row[17].isoformat() if row[17] else None,
    }


RESULT_FILTERS: dict[str, tuple[str, str]] = {
    "success": ("closed", "succeeded"),
    "failed": ("closed", "failed"),
    "awaiting": ("awaiting_approval", ""),
    "executing": ("executing", ""),
}


def list_deployments(
    settings: Settings,
    user: dict,
    *,
    limit: int = 50,
    offset: int = 0,
    created_from: str = "",
    created_to: str = "",
    deploy_type: str = "",
    result: str = "",
) -> dict:
    """部署任务列表；只做时间 / 类型 / 结果三项筛选。

    可见范围与排查任务共用同一条规则。类型不能只靠 SQL：历史任务的
    `deploy_type` 是空的，必须按标题/模板/命令推断；所以有类型筛选时
    先把候选全部取出，在 Python 里过滤后再按 offset/limit 切片。
    推不出类型的显示“未分类”，只在“全部”出现 —— 这是用户确认过的规则。

    `offset` 用于前端全量导出逐页拉取；`total` 是当前筛选后的总条数，
    不是本页条数。
    """
    clause = task_access.visible_clause(user)
    where = "task_type='deployment'"
    params: list[Any] = []
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))

    since = _parse_iso(created_from)
    until = _parse_iso(created_to)
    if since:
        where += " AND created_at >= %s"
        params.append(since)
    if until:
        where += " AND created_at <= %s"
        params.append(until)

    wanted_type = normalize_deploy_type(deploy_type)
    # 类型不能只靠 SQL：历史任务的 deploy_type 为空，要靠标题/命令推断。
    # 有类型筛选时先取全部候选，再在 Python 里推断并过滤；无类型时直接 SQL 分页。
    wanted_result = str(result or "").strip().lower()
    if wanted_result:
        if wanted_result not in RESULT_FILTERS:
            raise DeploymentError(f"result_invalid:{wanted_result}")
        status, outcome = RESULT_FILTERS[wanted_result]
        where += " AND status = %s"
        params.append(status)
        if outcome:
            where += " AND outcome = %s"
            params.append(outcome)

    if clause:
        if clause[0] == "FALSE":
            return {"items": [], "total": 0}
        where += f" AND {clause[0]}"
        params.extend(clause[1])

    with connect_app(settings) as conn:
        total = 0
        if not wanted_type:
            total = int(conn.execute(f"SELECT count(*) FROM v3_tasks WHERE {where}", params).fetchone()[0])
        query_params = list(params)
        query_limit = ""
        if not wanted_type:
            query_limit = "LIMIT %s OFFSET %s"
            query_params.extend([safe_limit, safe_offset])
        rows = conn.execute(
            f"""
            SELECT id, trace_id, status, outcome, reason_code, title,
                   target_scope, submitted_by, plan->>'plan_hash',
                   plan->>'template_id', created_at, closed_at,
                   plan, deploy_type
              FROM v3_tasks WHERE {where}
             ORDER BY created_at DESC, id DESC {query_limit}
            """,
            query_params,
        ).fetchall()

    items = []
    for row in rows:
        plan = row[12] if isinstance(row[12], dict) else {}
        code = deploy_type_of(row[5], plan, row[13])
        if wanted_type and code != wanted_type:
            continue
        items.append(
            {
                "id": str(row[0]),
                "trace_id": row[1],
                "status": row[2],
                "outcome": row[3],
                "reason_code": _normalize_reason_code(row[4]),
                "title": row[5],
                "target_scope": row[6] or {},
                "submitted_by": row[7],
                "plan_hash": row[8] or "",
                "template_id": row[9] or "",
                "created_at": row[10].isoformat() if row[10] else None,
                "closed_at": row[11].isoformat() if row[11] else None,
                "deploy_type": code,
                "deploy_type_label": describe_deploy_type(code),
            }
        )
    if wanted_type:
        total = len(items)
        items = items[safe_offset : safe_offset + safe_limit]
    return {"items": items, "total": total}


def _parse_iso(value: object) -> datetime | None:
    """筛选时间统一按 ISO 解析；空值或不合法就当没传，不让列表 500。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
