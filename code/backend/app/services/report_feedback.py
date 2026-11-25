"""逐条报告反馈：记录根因和每条结论的人工判断。

设计边界
--------
* 反馈绑定报告版本：提交时计算报告摘要，报告更新后旧反馈仍保留，
  前端据此提示“这条反馈可能对应旧版本”。
* 只存结论快照和纠正文本，不复制证据、命令或密钥。
* 同一个人对同一目标位置只保留一条最新反馈；换人则各存各的。
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from ..core.config import Settings
from ..core.db import connect_app

TARGET_TYPES = ("root_cause", "conclusion")
VERDICTS = ("confirm", "reject", "correct")
MAX_CORRECTED_LENGTH = 2000
MAX_NOTE_LENGTH = 1000
MAX_TARGET_TEXT_LENGTH = 2000


class FeedbackError(Exception):
    """反馈参数不合法；消息可直接给前端。"""


def _row_to_feedback(row) -> dict:
    """数据库行转接口字段。"""
    return {
        "id": str(row[0]),
        "task_id": str(row[1]),
        "target_type": str(row[2]),
        "target_index": int(row[3]),
        "target_text": str(row[4] or ""),
        "report_hash": str(row[5] or ""),
        "verdict": str(row[6]),
        "corrected_text": str(row[7] or ""),
        "note": str(row[8] or ""),
        "created_by": int(row[9]),
        "created_by_name": str(row[10] or ""),
        "created_at": row[11].isoformat() if row[11] else "",
        "updated_at": row[12].isoformat() if row[12] else "",
    }


def report_hash(report: dict) -> str:
    """结论内容摘要；只用于识别版本，不作为安全校验。

    只摘要根因、结论和方案，避免不同查看人看到的 `knowledge` 或轨迹
    差异把同一份报告的指纹算成两个。
    """
    payload = json.dumps({
        "root_cause": (report or {}).get("root_cause") or "",
        "conclusions": (report or {}).get("conclusions") or [],
        "solution": (report or {}).get("solution") or "",
        "verdict": (report or {}).get("verdict") or "",
    }, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _target_text(report: dict, target_type: str, target_index: int) -> str:
    """取反馈目标的当前文字；结论序号越界时提示报告可能已更新。"""
    if target_type == "root_cause":
        text = str(report.get("root_cause") or "").strip()
    else:
        conclusions = report.get("conclusions") or []
        if not isinstance(conclusions, list) or not (0 <= target_index < len(conclusions)):
            raise FeedbackError("结论序号不存在，报告可能已更新")
        item = conclusions[target_index]
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("conclusion") or "").strip()
        else:
            text = str(item or "").strip()
    return text[:MAX_TARGET_TEXT_LENGTH]


def submit_feedback(
    settings: Settings,
    task_id: str,
    user: dict,
    report: dict,
    *,
    target_type: str,
    target_index: int,
    verdict: str,
    corrected_text: str = "",
    note: str = "",
) -> dict:
    """写入一条反馈；同一人同一目标位置覆盖旧反馈。"""
    if target_type not in TARGET_TYPES:
        raise FeedbackError("反馈目标只能是根因或结论")
    if verdict not in VERDICTS:
        raise FeedbackError("反馈结论只能是准确、不准确或纠正")
    if target_type == "root_cause" and target_index != 0:
        raise FeedbackError("根因反馈的序号固定为 0")
    text = _target_text(report, target_type, int(target_index))
    if not text:
        raise FeedbackError("这条内容还没有可反馈的文字，请等报告生成后再试")
    corrected = (corrected_text or "").strip()
    if verdict == "correct" and not corrected:
        raise FeedbackError("选择纠正时请填写正确的内容")
    if len(corrected) > MAX_CORRECTED_LENGTH:
        raise FeedbackError(f"纠正内容最多 {MAX_CORRECTED_LENGTH} 字")
    if len(note) > MAX_NOTE_LENGTH:
        raise FeedbackError(f"备注最多 {MAX_NOTE_LENGTH} 字")
    actor_id = int(user["id"])
    actor_name = str(user.get("display_name") or user.get("username") or "未知用户")
    digest = report_hash(report)
    with connect_app(settings) as conn:
        row = conn.execute(
            """
            INSERT INTO v3_report_feedback
                (id, task_id, target_type, target_index, target_text, report_hash,
                 verdict, corrected_text, note, created_by, created_by_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (task_id, created_by, target_type, target_index)
            DO UPDATE SET
                target_text = EXCLUDED.target_text,
                report_hash = EXCLUDED.report_hash,
                verdict = EXCLUDED.verdict,
                corrected_text = EXCLUDED.corrected_text,
                note = EXCLUDED.note,
                updated_at = now()
            RETURNING id, task_id, target_type, target_index, target_text, report_hash,
                      verdict, corrected_text, note, created_by, created_by_name,
                      created_at, updated_at
            """,
            (
                uuid4(), task_id, target_type, int(target_index), text, digest,
                verdict, corrected, note.strip(), actor_id, actor_name,
            ),
        ).fetchone()
    if not row:
        raise FeedbackError("反馈写入失败")
    return _row_to_feedback(row)


def list_feedback(settings: Settings, task_id: str) -> list[dict]:
    """按目标位置返回该任务的全部反馈，前端再按当前用户过滤。"""
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, target_type, target_index, target_text, report_hash,
                   verdict, corrected_text, note, created_by, created_by_name,
                   created_at, updated_at
              FROM v3_report_feedback
             WHERE task_id = %s
             ORDER BY target_type, target_index, updated_at DESC
            """,
            (task_id,),
        ).fetchall()
    return [_row_to_feedback(row) for row in rows]


def list_feedback_for_tasks(settings: Settings, task_ids: list[str]) -> dict[str, list[dict]]:
    """批量读取多条任务的反馈，供知识库聚合一次查完，避免逐条任务查库。"""
    if not task_ids:
        return {}
    with connect_app(settings) as conn:
        rows = conn.execute(
            """
            SELECT id, task_id, target_type, target_index, target_text, report_hash,
                   verdict, corrected_text, note, created_by, created_by_name,
                   created_at, updated_at
              FROM v3_report_feedback
             WHERE task_id = ANY(%s::uuid[])
             ORDER BY task_id, target_type, target_index, updated_at DESC
            """,
            (task_ids,),
        ).fetchall()
    result: dict[str, list[dict]] = {}
    for row in rows:
        item = _row_to_feedback(row)
        result.setdefault(item["task_id"], []).append(item)
    return result


def feedback_notes(feedback: list[dict], limit: int = 8) -> list[str]:
    """把反馈压成知识条目可读的“人工复核”条目，最多保留 limit 条。"""
    notes: list[str] = []
    for item in feedback[:limit]:
        target = "根因" if item.get("target_type") == "root_cause" else (
            f"结论 {int(item.get('target_index') or 0) + 1}"
        )
        verdict = {"confirm": "已确认", "reject": "已标为不准确", "correct": "已纠正"}.get(
            str(item.get("verdict") or ""), str(item.get("verdict") or "")
        )
        reviewer = str(item.get("created_by_name") or "未知用户")
        line = f"{target}：{verdict}（{reviewer}）"
        corrected = str(item.get("corrected_text") or "").strip()
        if corrected:
            line += f"，正确内容：{corrected[:500]}"
        note = str(item.get("note") or "").strip()
        if note:
            line += f"，备注：{note[:200]}"
        notes.append(line)
    if len(feedback) > limit:
        notes.append(f"另有 {len(feedback) - limit} 条复核记录未展开")
    return notes
