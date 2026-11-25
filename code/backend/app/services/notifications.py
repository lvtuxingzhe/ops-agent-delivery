"""站内通知 + 飞书/邮件外发。

站内收件箱是主链路；外发渠道在站内通知写入成功后尽力发送，
失败只记日志，不影响排查/部署主流程。同一条通知的外发也按去重键
只发一次：`_insert` 返回 0 时不再调用渠道。
"""

from __future__ import annotations

import json
import logging
import os
import re

from ..core.config import Settings
from ..core.db import connect_app
from . import feishu_app, notification_channels, oncall
from .failure_taxonomy import REASON_TEXT, normalize_reason

logger = logging.getLogger(__name__)
MAX_LIMIT = 50


def _clip(value: str, limit: int) -> str:
    """通知只存短文本，避免把整份报告复制进通知表。"""
    return (value or "").strip()[:limit]


def _plain_text(value: object) -> str:
    """把报告里的字面量换行和代码围栏还原成纯文本。

    模型有时把换行写成两个字符 `\\n`，直接把这种文本丢进飞书会显示成
    一行 `\\n`；代码围栏在纯文本里也只是噪音，去掉围栏但保留命令。
    """
    text = str(value or "")
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    lines = [
        line for line in text.splitlines()
        if not line.strip().startswith("```")
    ]
    return "\n".join(lines).strip()


def _numbered_items(value: object, limit: int, width: int) -> list[str]:
    """把编号段落压成短句：每段取第一句和第一条命令，避免通知刷屏。"""
    text = _plain_text(value)
    items: list[str] = []
    for match in re.finditer(r"(?ms)^\s*\d+\.\s*(.*?)(?=^\s*\d+\.\s*|\Z)", text):
        body = match.group(1).strip()
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if not lines:
            continue
        head = lines[0].removeprefix("目的：").rstrip("：。")
        command = ""
        for line in lines[1:]:
            if line.startswith(("预期", "以上")) or line.endswith(("：", "。")):
                continue
            command = line
            break
        item = f"{head}：{command}" if command else head
        items.append(_clip(item, width))
        if len(items) >= limit:
            break
    return items


def _insert(
    conn,
    *,
    user_ids: list[int],
    type_: str,
    title: str,
    body: str,
    task_id: str | None,
    dedupe_key: str | None,
) -> int:
    """给多个收件人写同一类通知；没填 dedupe_key 时允许同一任务多条。"""
    if not user_ids:
        return 0
    return conn.execute(
        """
        INSERT INTO v3_notifications
            (user_id, type, title, body, task_id, dedupe_key)
        SELECT u.id, %s, %s, %s, %s::uuid, %s
          FROM v3_users u
         WHERE u.id = ANY(%s) AND u.is_active
        ON CONFLICT DO NOTHING
        """,
        (
            _clip(type_, 60),
            _clip(title, 120),
            _clip(body, 500),
            task_id,
            dedupe_key,
            user_ids,
        ),
    ).rowcount


def _task_title(conn, task_id: str) -> tuple[str, int | None] | None:
    """读任务标题和提交人；任务不存在时返回 None。"""
    row = conn.execute(
        "SELECT title, submitted_by FROM v3_tasks WHERE id = %s", (task_id,)
    ).fetchone()
    return (row[0] or "", row[1]) if row else None


def notify_report_ready(settings: Settings, *, task_id: str) -> int:
    """报告完成：通知提交人。提交人可能是空，空就不发。"""
    try:
        inserted = 0
        title = ""
        body = ""
        with connect_app(settings) as conn:
            with conn.transaction():
                task = _task_title(conn, task_id)
                if not task or task[1] is None:
                    return 0
                title = task[0] or "排查任务"
                report_row = conn.execute(
                    "SELECT report FROM v3_tasks WHERE id = %s", (task_id,),
                ).fetchone()
                digest = _report_digest(report_row[0] if report_row else None)
                body = f"排查「{title}」报告已完成。"
                if digest:
                    body += f"\n\n{digest}"
                inserted = _insert(
                    conn,
                    user_ids=[int(task[1])],
                    type_="task.report_ready",
                    title="排查报告已完成",
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"report_ready:{task_id}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="排查报告已完成", body=body,
            )
            _send_feishu_text(
                settings, title="排查报告已完成", body=body,
                user_ids=[int(task[1])] if task[1] is not None else None,
                task_id=task_id, icon="🔵",
            )
        return inserted
    except Exception:
        logger.exception("写报告完成通知失败 task_id=%s", task_id)
        return 0


def notify_comment_mention(
    settings: Settings, *, task_id: str, comment_id: str,
    mentioned_ids: list[int], actor_id: int,
) -> int:
    """讨论 @ 提醒：只提示到任务详情查看，不把评论正文带到通知渠道。"""
    recipients = sorted({int(item) for item in mentioned_ids if int(item) != int(actor_id)})
    if not recipients:
        return 0
    try:
        inserted = 0
        body = ""
        with connect_app(settings) as conn:
            with conn.transaction():
                task = conn.execute(
                    "SELECT title, submitted_by, assignee_id FROM v3_tasks WHERE id = %s",
                    (task_id,),
                ).fetchone()
                if not task:
                    return 0
                title = task[0] or "任务"
                # @ 可以写任意用户名，但只有能看见这条任务的人才该收到
                # 带任务标题的通知；否则等于借评论通知泄露任务存在性。
                visible = {
                    int(item) for item in (task[1], task[2])
                    if item is not None
                }
                privileged = {
                    int(row[0]) for row in conn.execute(
                        "SELECT id FROM v3_users WHERE id = ANY(%s) AND is_active "
                        "AND roles && ARRAY['admin','auditor']::text[]",
                        (recipients,),
                    ).fetchall()
                }
                recipients = sorted(set(recipients) & (visible | privileged))
                if not recipients:
                    return 0
                body = f"任务「{title}」的协作讨论里有人 @ 你，请打开任务详情查看。"
                inserted = _insert(
                    conn,
                    user_ids=recipients,
                    type_="task.comment_mention",
                    title="讨论里有人提到你",
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"comment_mention:{comment_id}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="讨论里有人提到你", body=body,
            )
            _send_feishu_text(
                settings, title="讨论里有人提到你", body=body,
                user_ids=recipients,
                task_id=task_id, icon="🔔",
            )
        return inserted
    except Exception:
        logger.exception("写讨论 @ 提醒失败 task_id=%s", task_id)
        return 0


def notify_task_assigned(
    settings: Settings, *, task_id: str, assignee_id: int,
    actor_id: int, previous_assignee: int | None = None,
) -> int:
    """任务转派：通知新处理人；失败不影响转派本身。"""
    try:
        inserted = 0
        body = ""
        with connect_app(settings) as conn:
            with conn.transaction():
                task = _task_title(conn, task_id)
                if not task:
                    return 0
                title = task[0] or "任务"
                actor_name = ""
                actor_row = conn.execute(
                    "SELECT display_name, username FROM v3_users WHERE id = %s",
                    (actor_id,),
                ).fetchone()
                if actor_row:
                    actor_name = actor_row[0] or actor_row[1] or ""
                body = f"任务「{title}」已转派给你处理。"
                if actor_name:
                    body += f"转派人：{actor_name}。"
                inserted = _insert(
                    conn,
                    user_ids=[int(assignee_id)],
                    type_="task.assigned",
                    title="有任务转派给你",
                    body=body,
                    task_id=task_id,
                    dedupe_key=None,
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="有任务转派给你", body=body,
            )
            _send_feishu_text(
                settings, title="有任务转派给你", body=body,
                user_ids=[int(assignee_id)],
                task_id=task_id, icon="📌",
            )
        return inserted
    except Exception:
        logger.exception(
            "写任务转派通知失败 task_id=%s assignee_id=%s", task_id, assignee_id,
        )
        return 0


def _approval_recipients(conn, task_id: str) -> tuple[list[int], str]:
    """审批通知收件人：当前值班 + 已指派审批人，都没有才群发管理员。

    返回（用户 ID 列表，给正文用的说明）。值班表为空时不阻塞审批，
    回落到原有全体管理员行为。
    """
    recipients: list[int] = []
    notes: list[str] = []
    shifts = oncall.current_oncall(conn)
    for shift in shifts:
        uid = int(shift["user_id"])
        if uid not in recipients:
            recipients.append(uid)
        name = shift.get("user_name") or f"用户 {uid}"
        notes.append(name)
    if notes:
        notes_text = "当前审批值班：" + "、".join(notes) + "。"
    else:
        notes_text = ""

    row = conn.execute(
        "SELECT approval FROM v3_tasks WHERE id = %s",
        (task_id,),
    ).fetchone()
    approval = row[0] if row and isinstance(row[0], dict) else {}
    routing = approval.get("routing") if isinstance(approval.get("routing"), dict) else {}
    assigned = routing.get("assigned_to")
    try:
        assigned_id = int(assigned) if assigned is not None else 0
    except (TypeError, ValueError):
        assigned_id = 0
    if assigned_id and assigned_id not in recipients:
        recipients.append(assigned_id)
        notes_text += f" 已指派给用户 {assigned_id}。"

    if not recipients:
        admin_rows = conn.execute(
            "SELECT id FROM v3_users WHERE is_active "
            "AND roles @> ARRAY['admin']::text[]"
        ).fetchall()
        recipients = [int(item[0]) for item in admin_rows]
        if not recipients:
            notes_text = ""
        elif notes_text:
            notes_text += " 当前没有值班安排，已发给全部管理员。"
        else:
            notes_text = "当前没有值班安排，已发给全部管理员。"
    return recipients, notes_text


def _report_digest(report: object) -> str:
    """把报告压成飞书可读的结论、根因和处置方案。"""
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except json.JSONDecodeError:
            return ""
    if not isinstance(report, dict):
        return ""
    headline = _plain_text(report.get("headline") or report.get("primary_answer") or "")
    root_cause = _plain_text(report.get("root_cause") or "")
    solution = _plain_text(report.get("solution") or "")
    conclusions = report.get("conclusions")
    if not headline and isinstance(conclusions, list):
        texts = [
            _plain_text(item.get("text") or "")
            for item in conclusions[:3]
            if isinstance(item, dict)
        ]
        headline = "；".join(item for item in texts if item)
    lines: list[str] = []
    if headline:
        lines.append(f"结论：{_clip(headline, 220)}")
    if root_cause:
        root_items = _numbered_items(root_cause, 1, 180)
        root_text = "；".join(root_items) if root_items else _clip(root_cause, 260)
        lines.append(f"根因：{root_text}")
    if solution:
        steps = _numbered_items(solution, 3, 220)
        if steps:
            numbered = "\n".join(
                f"{index}. {item}" for index, item in enumerate(steps, start=1)
            )
            lines.append(f"处置方案：\n{numbered}")
        else:
            lines.append(f"处置方案：{_clip(solution, 400)}")
    return "\n".join(lines)


def _plan_commands(plan: object) -> str:
    """只取部署方案的 apply 命令；不碰账号、密码等目标信息。"""
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except json.JSONDecodeError:
            return ""
    if not isinstance(plan, dict):
        return ""
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return ""
    lines: list[str] = []
    for item in operations[:10]:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            argv = item.get("argv")
            command = " ".join(str(part) for part in argv) if isinstance(argv, list) else ""
        if not command:
            continue
        command = _plain_text(command)
        title = str(item.get("title") or "").strip()
        scope = str(item.get("scope") or "").strip()
        suffix = "（高危）" if scope == "dangerous" else ""
        lines.append(f"{len(lines) + 1}. {title + '：' if title else ''}{_clip(command, 300)}{suffix}")
    return "\n".join(lines)


def _send_feishu_text(
    settings: Settings,
    *,
    title: str,
    body: str,
    user_ids: list[int] | None = None,
    oncall_scope: str = "alert",
    task_id: str = "",
    link_path: str = "",
    icon: str = "",
) -> None:
    """飞书应用启用时补一条文本；失败只记日志，不影响站内通知。"""
    try:
        if task_id:
            base = (
                os.environ.get("NOTIFY_BASE_URL")
                or os.environ.get("FRONTEND_URL")
                or "http://127.0.0.1:5173"
            ).rstrip("/")
            path = link_path or f"/#/tasks/{task_id}"
            body = f"{body}\n\n查看：{base}{path}"
        result = feishu_app.notify_text(
            settings,
            title=title,
            body=body,
            user_ids=user_ids,
            oncall_scope=oncall_scope,
            icon=icon,
        )
        if not result.get("ok"):
            logger.info(
                "飞书文本通知未发送 title=%s detail=%s",
                title, result.get("detail") or result.get("items"),
            )
    except Exception:
        logger.exception("飞书文本通知发送失败 title=%s", title)


def _send_feishu_approval_card(settings: Settings, *, task_id: str) -> None:
    """飞书应用启用时补一张可操作卡片；失败只记日志，不影响站内通知。"""
    try:
        result = feishu_app.send_approval_card(settings, task_id=task_id)
        if not result.get("ok"):
            logger.info(
                "飞书审批卡片未发送 task_id=%s detail=%s",
                task_id, result.get("detail"),
            )
    except Exception:
        logger.exception("飞书审批卡片发送失败 task_id=%s", task_id)


def notify_approval(settings: Settings, *, task_id: str) -> int:
    """进入待审批：通知所有启用中的管理员。"""
    try:
        inserted = 0
        body = ""
        with connect_app(settings) as conn:
            with conn.transaction():
                task = _task_title(conn, task_id)
                if not task:
                    return 0
                title = task[0] or "排查任务"
                body = f"排查「{title}」已进入处置审批，请只审批要执行的命令。"
                user_ids, route_note = _approval_recipients(conn, task_id)
                if route_note:
                    body += f" {route_note}"
                inserted = _insert(
                    conn,
                    user_ids=user_ids,
                    type_="task.approval_pending",
                    title="有新的命令待审批",
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"approval_pending:{task_id}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="有新的命令待审批", body=body,
            )
            _send_feishu_approval_card(settings, task_id=task_id)
        return inserted
    except Exception:
        logger.exception("写待审批通知失败 task_id=%s", task_id)
        return 0


def notify_approval_timeout(settings: Settings, *, task_id: str,
                            overdue_minutes: int = 0) -> int:
    """审批超时：通知所有启用中的管理员接管或驳回。

    去重键按任务固定，同一条任务超时后只提醒一次；任务后续被接管或结束，
    这条提醒自然失去意义，不需要再补发。
    """
    try:
        inserted = 0
        body = ""
        with connect_app(settings) as conn:
            with conn.transaction():
                task = _task_title(conn, task_id)
                if not task:
                    return 0
                title = task[0] or "任务"
                overdue = max(0, int(overdue_minutes))
                suffix = f"已超过审批时限 {overdue} 分钟" if overdue else "已超过审批时限"
                body = f"「{title}」{suffix}仍未处理，请管理员接管或驳回。"
                user_ids, route_note = _approval_recipients(conn, task_id)
                if route_note:
                    body += f" {route_note}"
                inserted = _insert(
                    conn,
                    user_ids=user_ids,
                    type_="task.approval_timeout",
                    title="审批已超时",
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"approval_timeout:{task_id}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="审批已超时", body=body,
                link_path=f"/#/approvals?task={task_id}",
            )
        return inserted
    except Exception:
        logger.exception("写审批超时通知失败 task_id=%s", task_id)
        return 0


def notify_deployment_approver_assigned(
    settings: Settings,
    *,
    task_id: str,
    approver_id: int,
    actor_id: int,
    reason: str = "",
) -> int:
    """把「方案已指派给你审批」发给指定审批人；失败不影响指派本身。"""
    try:
        with connect_app(settings) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT title, submitted_by, task_type FROM v3_tasks WHERE id = %s",
                    (task_id,),
                ).fetchone()
                if not row or str(row[2] or "") != "deployment":
                    return 0
                title = row[0] or "部署任务"
                body = f"部署「{title}」已指派给你审批。"
                if reason:
                    body += f"理由：{reason[:120]}"
                inserted = _insert(
                    conn,
                    user_ids=[int(approver_id)],
                    type_="deployment.approval.assigned",
                    title="部署方案待你审批",
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"deployment_assign:{task_id}:{approver_id}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title="部署方案待你审批", body=body,
                link_path=f"/#/deployments?task={task_id}",
            )
            _send_feishu_approval_card(settings, task_id=task_id)
        return inserted
    except Exception:
        logger.exception("写部署指派通知失败 task_id=%s approver_id=%s", task_id, approver_id)
        return 0


def notify_deployment_result(settings: Settings, *, task_id: str) -> int:
    """部署结束通知：提交人和管理员都能看到成功/失败/驳回。

    通知写库失败或外发失败都不影响部署主流程；去重键按任务和结果，
    同一条任务重复结束不会反复提醒。
    """
    try:
        inserted = 0
        title = ""
        body = ""
        outcome = "failed"
        with connect_app(settings) as conn:
            with conn.transaction():
                row = conn.execute(
                    "SELECT title, submitted_by, outcome, reason_code, task_type, approval, plan "
                    "FROM v3_tasks WHERE id = %s",
                    (task_id,),
                ).fetchone()
                if not row or str(row[4] or "") != "deployment":
                    return 0
                title = row[0] or "部署任务"
                submitted_by = row[1]
                outcome = str(row[2] or "failed")
                reason_code = str(row[3] or "")
                approval = row[5] if isinstance(row[5], dict) else {}
                plan = row[6] if isinstance(row[6], dict) else {}
                routing = approval.get("routing") if isinstance(approval.get("routing"), dict) else {}
                assigned_to = routing.get("assigned_to")

                if outcome == "succeeded":
                    notification_type = "deployment.result.succeeded"
                    notification_title = "部署执行成功"
                    body = f"部署「{title}」执行成功，全部变更与验证通过。"
                elif outcome == "rejected":
                    notification_type = "deployment.result.rejected"
                    notification_title = "部署已驳回"
                    body = f"部署「{title}」已被驳回，任务已关闭。"
                elif outcome == "partial":
                    notification_type = "deployment.result.failed"
                    notification_title = "部署部分成功"
                    reason = REASON_TEXT.get(
                        normalize_reason(reason_code), "部分命令执行失败",
                    )
                    body = f"部署「{title}」部分成功：{reason}。"
                else:
                    notification_type = "deployment.result.failed"
                    notification_title = "部署执行失败"
                    reason = REASON_TEXT.get(
                        normalize_reason(reason_code), "执行未通过",
                    )
                    body = f"部署「{title}」执行失败：{reason}。"

                commands = _plan_commands(plan)
                if commands:
                    body += f"\n\n部署命令：\n{commands}"

                admin_ids = [
                    int(item[0]) for item in conn.execute(
                        "SELECT id FROM v3_users WHERE is_active "
                        "AND roles @> ARRAY['admin']::text[]"
                    ).fetchall()
                ]
                recipients = admin_ids
                if submitted_by is not None:
                    recipients = [*recipients, int(submitted_by)]
                if assigned_to is not None:
                    recipients = [*recipients, int(assigned_to)]
                inserted = _insert(
                    conn,
                    user_ids=recipients,
                    type_=notification_type,
                    title=notification_title,
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"deployment_result:{task_id}:{outcome}",
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id,
                title=notification_title, body=body,
                link_path=f"/#/deployments?tab=records&task={task_id}",
            )
            _send_feishu_text(
                settings, title=notification_title, body=body,
                user_ids=[
                    int(item) for item in (submitted_by, assigned_to)
                    if item is not None
                ],
                task_id=task_id,
                link_path=f"/#/deployments?tab=records&task={task_id}",
                icon={
                    "succeeded": "🟢",
                    "partial": "🟡",
                    "rejected": "🚫",
                }.get(outcome, "🔴"),
            )
        return inserted
    except Exception:
        logger.exception("写部署结果通知失败 task_id=%s", task_id)
        return 0


def notify_monitor_alert(
    settings: Settings, *, alert_key: str, title: str, body: str,
    resolved: bool = False, task_id: str | None = None,
    event_key: str | None = None,
) -> int:
    """监控告警 / 恢复：给所有启用中的管理员写站内通知，并外发。

    `event_key` 用于区分同一条告警的不同 firing 事件；`alert_key` 用稳定的
    group_key，保证两个 Prometheus 报同一条告警只发一条通知。
    `task_id` 有值时，通知点开就能跳到自动创建的排查任务。
    """
    try:
        inserted = 0
        notification_type = "monitor.recovery" if resolved else "monitor.alert"
        dedupe_prefix = "monitor_recovery" if resolved else "monitor_alert"
        dedupe_subject = event_key or alert_key
        # 前端是 hash 路由；外发渠道没有站内 task_id 字段，正文里带 hash 链接，
        # 站内仍按 task_id 跳转。链接放最前面，避免正文过长时被截断。
        if task_id and "/#/tasks/" not in body:
            body = f"任务：/#/tasks/{task_id}\n{body}"
        with connect_app(settings) as conn:
            with conn.transaction():
                admin_ids = [
                    int(row[0]) for row in conn.execute(
                        "SELECT id FROM v3_users WHERE is_active "
                        "AND roles @> ARRAY['admin']::text[]"
                    ).fetchall()
                ]
                inserted = _insert(
                    conn,
                    user_ids=admin_ids,
                    type_=notification_type,
                    title=title,
                    body=body,
                    task_id=task_id,
                    dedupe_key=f"{dedupe_prefix}:{dedupe_subject}",
                )
        if inserted:
            notification_channels.deliver_alert_event(
                settings, title=title, body=body,
            )
            _send_feishu_text(
                settings, title=title, body=body,
                oncall_scope="alert",
                icon="🟢" if resolved else "🔴",
            )
        return inserted
    except Exception:
        logger.exception("写监控告警通知失败 key=%s resolved=%s", alert_key, resolved)
        return 0


def notify_archive_failure(
    settings: Settings, *, day: str, reason: str, notify_key: str,
) -> int:
    """审计归档失败：只通知管理员。同一天同一类故障只提醒一次。"""
    try:
        inserted = 0
        body = f"审计归档失败：{day}，原因 {reason}。业务写入未受影响，请检查归档桶与凭据。"
        with connect_app(settings) as conn:
            with conn.transaction():
                admin_ids = [
                    int(row[0]) for row in conn.execute(
                        "SELECT id FROM v3_users WHERE is_active "
                        "AND roles @> ARRAY['admin']::text[]"
                    ).fetchall()
                ]
                inserted = _insert(
                    conn,
                    user_ids=admin_ids,
                    type_="audit.archive_failed",
                    title="审计归档失败",
                    body=body,
                    task_id=None,
                    dedupe_key=f"audit_archive_failed:{notify_key}",
                )
        if inserted:
            notification_channels.deliver_alert_event(
                settings, title="审计归档失败", body=body,
            )
        return inserted
    except Exception:
        logger.exception("写审计归档失败通知失败 key=%s", notify_key)
        return 0


def notify_token_budget(
    settings: Settings, *, task_id: str, title: str, body: str,
    dedupe_key: str,
) -> int:
    """Token 预算超限：通知任务提交人和所有启用中的管理员。

    预算只提醒、不拦截任务；同一条任务或同一个自然日只提醒一次。
    """
    try:
        inserted = 0
        with connect_app(settings) as conn:
            with conn.transaction():
                task = _task_title(conn, task_id)
                if not task:
                    return 0
                recipients = [
                    int(row[0]) for row in conn.execute(
                        "SELECT id FROM v3_users WHERE is_active "
                        "AND roles @> ARRAY['admin']::text[]"
                    ).fetchall()
                ]
                if task[1] is not None:
                    recipients.append(int(task[1]))
                inserted = _insert(
                    conn,
                    user_ids=sorted(set(recipients)),
                    type_="token.budget_exceeded",
                    title=title,
                    body=body,
                    task_id=task_id,
                    dedupe_key=dedupe_key,
                )
        if inserted:
            notification_channels.deliver_task_event(
                settings, task_id=task_id, title=title, body=body,
            )
        return inserted
    except Exception:
        logger.exception("写 Token 预算通知失败 task_id=%s", task_id)
        return 0


def list_notifications(
    settings: Settings, *, user_id: int, unread_only: bool = False,
    limit: int = 20,
) -> dict:
    """当前用户的收件箱；同时返回未读数。"""
    limit = max(1, min(int(limit), MAX_LIMIT))
    with connect_app(settings) as conn:
        where = "user_id=%s" + (" AND read_at IS NULL" if unread_only else "")
        rows = conn.execute(
            "SELECT id, type, title, body, task_id, read_at, created_at "
            f"FROM v3_notifications WHERE {where} "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
        unread = conn.execute(
            "SELECT count(*) FROM v3_notifications "
            "WHERE user_id=%s AND read_at IS NULL",
            (user_id,),
        ).fetchone()[0]
    return {
        "items": [
            {
                "id": int(row[0]),
                "type": row[1],
                "title": row[2],
                "body": row[3],
                "task_id": str(row[4]) if row[4] else None,
                "read_at": row[5].isoformat() if row[5] else None,
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ],
        "unread_count": int(unread),
    }


def mark_read(settings: Settings, *, user_id: int, notification_id: int) -> dict | None:
    """标记单条已读；不是自己的通知返回 None，避免越权改别人收件箱。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            row = conn.execute(
                "UPDATE v3_notifications SET read_at=COALESCE(read_at, now()) "
                "WHERE id=%s AND user_id=%s RETURNING id, read_at",
                (notification_id, user_id),
            ).fetchone()
    if not row:
        return None
    return {"id": int(row[0]), "read_at": row[1].isoformat() if row[1] else None}


def mark_all_read(settings: Settings, *, user_id: int) -> int:
    """一键全部已读，返回本次更新条数。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            result = conn.execute(
                "UPDATE v3_notifications SET read_at=now() "
                "WHERE user_id=%s AND read_at IS NULL",
                (user_id,),
            )
    return int(result.rowcount or 0)


def mark_task_read(settings: Settings, *, user_id: int, task_id: str) -> int:
    """打开某条任务/报告详情时，把该任务下自己的未读通知全部标已读。"""
    with connect_app(settings) as conn:
        with conn.transaction():
            result = conn.execute(
                "UPDATE v3_notifications SET read_at=now() "
                "WHERE user_id=%s AND task_id=%s::uuid AND read_at IS NULL",
                (user_id, task_id),
            )
    return int(result.rowcount or 0)

