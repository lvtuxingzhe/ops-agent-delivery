"""任务与报告仓储：只做数据库读写和响应白名单。"""

from __future__ import annotations

from typing import Any

from ...core.config import Settings
from ...core.contract import data_quality_text, status_text
from ...core.db import connect_app
from ...core.redaction import RedactionSession, restore_text
from .. import knowledge_service
from .constants import EVIDENCE_EXCERPT_MAX
from .public import public_evidence, public_knowledge, public_trace


def _task_row_to_response(
    task_row: tuple,
    submitted_by_name: str | None = None,
    approval_expires_at: str | None = None,
    confirmed_by: int | None = None,
    confirmed_by_name: str | None = None,
    approved_at: str | None = None,
    approval_reason: str | None = None,
) -> dict:
    """任务响应白名单：不整行 dump JSONB，只给该看的字段。"""
    return {
        "id": str(task_row[0]),
        "trace_id": task_row[1],
        "task_type": task_row[2],
        "status": task_row[3],
        "status_text": status_text(task_row[3]),
        "outcome": task_row[4],
        "stage": task_row[5],
        "title": task_row[6],
        "submitted_by": task_row[7],
        # 提交人在库里是用户 ID；给界面单列展示名，避免出现“提交人 1”。
        "submitted_by_name": (submitted_by_name or "").strip() or None,
        "created_at": task_row[8].isoformat() if task_row[8] else None,
        "closed_at": task_row[9].isoformat() if task_row[9] else None,
        # 审批人/审批时间/审批理由只给展示字段，不回传令牌或许可。
        "confirmed_by": confirmed_by,
        "confirmed_by_name": (confirmed_by_name or "").strip() or None,
        "approved_at": approved_at or None,
        "approval_reason": (approval_reason or "").strip() or None,
        # 只暴露审批截止时间，不回传 approval 里的许可、令牌等内部字段。
        "approval_expires_at": approval_expires_at or None,
    }


_TASK_COLUMNS = (
    "id, trace_id, task_type, status, outcome, stage, title, "
    "submitted_by, created_at, closed_at"
)


def get_task(settings: Settings, task_id: str) -> dict | None:
    """按任务号查一条任务，查不到返回 None。"""
    task_columns = ", ".join(f"t.{column.strip()}" for column in _TASK_COLUMNS.split(","))
    with connect_app(settings) as conn:
        task_row = conn.execute(
            f"SELECT {task_columns}, "
            "COALESCE(NULLIF(su.display_name, ''), su.username, '') "
            "FROM v3_tasks t LEFT JOIN v3_users su ON su.id = t.submitted_by "
            "WHERE t.id = %s",
            (task_id,),
        ).fetchone()
    return (
        _task_row_to_response(task_row, submitted_by_name=task_row[10])
        if task_row else None
    )


def _restore_session(redaction_map: dict | None) -> RedactionSession | None:
    """从库里的 forward 映射重建只读会话；没有映射就返回 None。"""
    if not isinstance(redaction_map, dict):
        return None
    forward = redaction_map.get("forward")
    if not isinstance(forward, dict) or not forward:
        return None
    session = RedactionSession(task_id=str(redaction_map.get("task_id") or ""))
    session.forward = {str(key): str(value) for key, value in forward.items()}
    session.reverse = {value: key for key, value in session.forward.items()}
    return session


def _restore_value(value: Any, session: RedactionSession | None) -> Any:
    if session is None or not isinstance(value, str):
        return value
    restored = restore_text(value, session)
    return value if restored is None else restored


def _restore_report(report: dict, session: RedactionSession | None) -> dict:
    """查看接口按映射还原；导出接口不走这里，保留占位符。"""
    if session is None:
        return report
    result = dict(report)
    for key in ("headline", "scope", "summary", "root_cause", "solution",
                "next_step", "root_cause_note", "primary_answer"):
        if key in result:
            result[key] = _restore_value(result[key], session)
    result["inventory"] = [
        {
            **item,
            "service": _restore_value(item.get("service", ""), session),
            "ports": [_restore_value(port, session) for port in (item.get("ports") or [])],
        }
        for item in (result.get("inventory") or [])
        if isinstance(item, dict)
    ]
    result["incidental_findings"] = [
        {
            **item,
            "object": _restore_value(item.get("object", ""), session),
            "finding": _restore_value(item.get("finding", ""), session),
            "impact": _restore_value(item.get("impact", ""), session),
        }
        for item in (result.get("incidental_findings") or [])
        if isinstance(item, dict)
    ]
    result["host_coverage"] = [
        {
            **item,
            "reason": _restore_value(item.get("reason", ""), session),
        }
        for item in (result.get("host_coverage") or [])
        if isinstance(item, dict)
    ]
    result["hypotheses"] = [
        {
            **item,
            "statement": _restore_value(item.get("statement", ""), session),
            "note": _restore_value(item.get("note", ""), session),
        }
        for item in (result.get("hypotheses") or [])
        if isinstance(item, dict)
    ]
    result["conclusions"] = [
        {
            **item,
            "text": _restore_value(item.get("text", ""), session),
        }
        for item in (result.get("conclusions") or [])
        if isinstance(item, dict)
    ]
    result["unknowns"] = [
        _restore_value(item, session) for item in (result.get("unknowns") or [])
    ]
    return result


def get_report(settings: Settings, task_id: str, *,
               viewer: dict | None = None, restore: bool = True) -> dict | None:
    """按任务号取报告草稿，绝不整行 dump 数据库内容。

    `viewer` 是 HTTP 查看人；传了就在读取时按历史任务可见性再过滤
    `knowledge`，避免任务转交后原样展示别人的历史摘要。内部 worker /
    评估脚本不传，保持原有全量行为。
    `restore=False` 用于导出：报告里保留 KEY-n / IP-n 占位符，避免密钥
    随文件二次扩散。
    """
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status, report, evidences, redaction_map FROM v3_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    report = row[1] if isinstance(row[1], dict) else {}
    raw_evidences = row[2] if isinstance(row[2], list) else []
    redaction_map = row[3] if isinstance(row[3], dict) else {}
    evidences = [
        public_evidence(item) for item in raw_evidences
        if isinstance(item, dict) and item.get("id")
    ][:100]
    hypotheses = [
        item for item in (report.get("hypotheses") or [])
        if isinstance(item, dict)
    ][:6]
    supported_evidence_ids = sorted({
        str(evidence_id)
        for item in hypotheses
        if item.get("status") == "supported"
        for evidence_id in (item.get("evidence_ids") or [])
        if str(evidence_id).strip()
    })
    root_cause = str(report.get("root_cause") or "")
    root_cause_verified = report.get("root_cause_verified")
    if root_cause_verified is None:
        root_cause_verified = bool(
            root_cause
            and str(report.get("verdict") or "") == "anomaly"
            and supported_evidence_ids
        )
    stored_verification_ids = [
        str(item) for item in (report.get("root_cause_evidence_ids") or []) if str(item).strip()
    ]
    result = {
        "task_id": str(task_id),
        "status": row[0],
        "data_quality": data_quality_text(report.get("data_quality", "no_data")),
        "answer_mode": report.get("answer_mode", ""),
        "answer_quality": report.get("answer_quality", {}),
        "primary_answer": report.get("primary_answer", ""),
        "inventory": report.get("inventory", []),
        "incidental_findings": report.get("incidental_findings", []),
        "host_coverage": report.get("host_coverage", []),
        "headline": report.get("headline", ""),
        "verdict": report.get("verdict", "insufficient"),
        "scope": report.get("scope", ""),
        "summary": report.get("summary", ""),
        "hypotheses": hypotheses,
        "root_cause": root_cause,
        "root_cause_verified": bool(root_cause_verified),
        "root_cause_evidence_ids": stored_verification_ids or supported_evidence_ids,
        "root_cause_source": report.get("root_cause_source", "ai"),
        "root_cause_note": report.get("root_cause_note", ""),
        "solution": report.get("solution", ""),
        "report_complete": bool(report.get(
            "report_complete",
            str(report.get("verdict") or "") != "anomaly",
        )),
        "report_gaps": [
            str(item) for item in (report.get("report_gaps") or [])
        ][:5],
        "next_step": report.get("next_step", ""),
        "conclusions": report.get("conclusions", []),
        "unknowns": report.get("unknowns", []),
        "trace": public_trace(report.get("trace", [])),
        "knowledge": knowledge_service.filter_visible(
            settings, public_knowledge(report.get("knowledge", [])), viewer,
        ),
        "generated_by": report.get("generated_by", ""),
        "fake": report.get("fake", True),
        "conclusions_count": len(report.get("conclusions", [])),
        "evidences": evidences,
        "evidence_count": len(evidences),
    }
    if not restore:
        return result
    return _restore_report(result, _restore_session(redaction_map))

def _load_screenshot_evidence(settings: Settings, task_id: str) -> list[dict]:
    """把人工截图变成报告里的证据；只取脱敏文本和缩略图。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, ocr_text, ocr_confidence, data_status,
                   thumbnail_uri, created_at
              FROM v3_task_screenshots
             WHERE task_id = %s
             ORDER BY created_at, id
            """,
            (task_id,),
        ).fetchall()
    result = []
    for row in rows:
        result.append({
            "id": f"screenshot:{row[0]}",
            "source": "manual_screenshot",
            "observed_at": row[6].isoformat() if row[6] else "",
            "target": row[1],
            "data_status": row[4] or "unknown",
            "fake": False,
            "excerpt": str(row[2] or "")[:EVIDENCE_EXCERPT_MAX],
            "ocr_confidence": float(row[3] or 0),
            "thumbnail": str(row[5] or ""),
        })
    return result

def _task_status(settings: Settings, task_id: str) -> str:
    """执行器轮询任务状态：API 终止后这里会立刻看到 closed。"""
    with connect_app(settings) as conn:
        row = conn.execute(
            "SELECT status FROM v3_tasks WHERE id = %s", (task_id,),
        ).fetchone()
    return row[0] if row else ""
