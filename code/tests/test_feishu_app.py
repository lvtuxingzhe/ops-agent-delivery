"""飞书应用卡片：配置兼容、卡片结构和高危确认。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import feishu_app, notifications, oncall


def test_chat_ids_supports_legacy_single_chat():
    """旧配置只有 chat_id 时，仍当成一个目标群。"""
    assert feishu_app._chat_ids({"chat_id": "oc_old"}) == ["oc_old"]


def test_chat_ids_prefers_new_list_and_caps():
    """新配置优先读 chat_ids，并限制最多 20 个群。"""
    value = {"chat_ids": [f"oc_{index}" for index in range(25)], "chat_id": "oc_old"}
    items = feishu_app._chat_ids(value)
    assert len(items) == 20
    assert items[0] == "oc_0"
    assert "oc_old" not in items


def test_public_config_does_not_return_secret_values():
    """配置页只能看到“已配置”，不能把加密后的秘密原样带出去。"""
    item = feishu_app._public({
        "app_id": "cli_test",
        "app_secret_enc": "encrypted-secret",
        "chat_ids": ["oc_a", "oc_b"],
    }, True)
    assert item["app_secret_set"] is True
    assert item["chat_ids"] == ["oc_a", "oc_b"]
    assert "app_secret_enc" not in item


def test_approval_card_has_approve_and_reject_buttons():
    """普通命令卡片有批准和驳回两个动作，按钮里带任务编号。"""
    card = feishu_app._approval_card({
        "title": "磁盘排查",
        "platform": "linux",
        "host": "host-150",
        "reason": "需要清理",
        "operations": [{"argv": ["df", "-h"], "scope": "read"}],
    }, task_id="40e1f9ae-a30c-46f5-93f0-562a8fb92cca", editable=True)
    actions = next(item["actions"] for item in card["elements"] if item["tag"] == "action")
    buttons = {
        button["value"]["action"]: button
        for button in actions if "value" in button
    }
    assert set(buttons) == {"approve", "reject"}
    assert buttons["approve"]["value"]["task_id"] == "40e1f9ae-a30c-46f5-93f0-562a8fb92cca"
    assert "confirm" not in buttons["approve"]


def test_approval_card_dangerous_needs_extra_confirm():
    """高危命令的批准按钮必须再弹一次确认。"""
    card = feishu_app._approval_card({
        "title": "重启服务",
        "platform": "linux",
        "host": "host-150",
        "reason": "",
        "operations": [{"argv": ["systemctl", "restart", "nginx"], "scope": "dangerous"}],
    }, task_id="40e1f9ae-a30c-46f5-93f0-562a8fb92cca", editable=True)
    actions = next(item["actions"] for item in card["elements"] if item["tag"] == "action")
    approve = next(button for button in actions if button["value"]["action"] == "approve")
    assert approve["confirm"]["title"]["content"] == "批准高危命令？"
    assert card["header"]["template"] == "orange"


def test_approval_card_without_operations_has_no_action_buttons():
    """没有处置命令时只给打开平台，不给空批准按钮。"""
    card = feishu_app._approval_card({
        "title": "空方案",
        "platform": "",
        "host": "local",
        "reason": "",
        "operations": [],
    }, task_id="40e1f9ae-a30c-46f5-93f0-562a8fb92cca", editable=False)
    actions = next(item["actions"] for item in card["elements"] if item["tag"] == "action")
    assert [button["text"]["content"] for button in actions] == ["打开平台"]


def test_mention_parts_at_bound_and_name_unbound():
    """绑定的人用 open_id 真 @；未绑定的只写名字并说明无法 @。"""
    rows = [
        (1, "管理员", "ou_demo_admin_open_id"),
        (2, "李四", None),
    ]
    text = feishu_app._mention_parts(rows, [1, 2])
    assert '<at user_id="ou_demo_admin_open_id"></at>' in text
    assert "李四（未绑定飞书，无法 @）" in text


def test_mention_parts_deduplicates_user_ids():
    """重复用户只 @ 一次，避免一条消息刷屏。"""
    rows = [(1, "管理员", "ou_demo_admin_open_id")]
    text = feishu_app._mention_parts(rows, [1, 1, 0])
    assert text.count("<at ") == 1


def test_merge_mention_ids_keeps_explicit_and_oncall():
    """事件相关人和当前值班人都要 @，重复的只保留一次。"""
    items = feishu_app._merge_mention_ids([3, 1, 1], [2, 3])
    assert items == [3, 1, 2]


def test_send_text_to_chats_collects_message_ids(monkeypatch):
    """多群发送结果要带消息编号，方便排查“到底发出去没有”。"""
    monkeypatch.setattr(
        feishu_app, "send_text",
        lambda settings, chat_id, text: {"message_id": "om_test"},
    )
    result = feishu_app.send_text_to_chats(None, chat_ids=["oc_a"], text="hi")
    assert result["ok"] is True
    assert result["message_ids"] == ["om_test"]


def test_notify_text_prefixes_status_icon(monkeypatch):
    """状态色标放在标题前；纯文本不能改字号颜色，但 emoji 能一眼区分结果。"""
    class _Conn:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    captured = {}

    def fake_send(settings, chat_ids, text):
        captured["text"] = text
        return {"ok": True, "items": [], "message_ids": ["om_test"]}

    monkeypatch.setattr(feishu_app, "_load_raw", lambda settings: ({"chat_ids": ["oc_a"]}, True))
    monkeypatch.setattr(feishu_app, "connect_app", lambda settings: _Conn())
    monkeypatch.setattr(feishu_app, "_oncall_ids", lambda conn, scope: [])
    monkeypatch.setattr(feishu_app, "_mention_text", lambda conn, ids: "")
    monkeypatch.setattr(feishu_app, "send_text_to_chats", fake_send)
    result = feishu_app.notify_text(None, title="部署成功", body="命令已执行", icon="🟢")
    assert captured["text"] == "🟢 部署成功\n\n命令已执行"
    assert result["message_ids"] == ["om_test"]


def test_parse_group_command_supports_aliases_and_mentions():
    """群消息里的 @占位符要先去掉，再识别中文命令和斜杠命令。"""
    assert feishu_app._parse_command("@_user_1 状态") == ("status", "")
    assert feishu_app._parse_command("/状态 40e1f9ae-a30c-46f5-93f0-562a8fb92cca")
    assert feishu_app._parse_command("@_user_1 重跑排查 40e1f9ae-a30c-46f5-93f0-562a8fb92cca")
    assert feishu_app._parse_command("/确认重跑 40e1f9ae-a30c-46f5-93f0-562a8fb92cca") == ("", "")
    assert feishu_app._parse_command("/help") == ("help", "")
    assert feishu_app._parse_command("你好") == ("", "")
    assert "/确认重跑" not in feishu_app._command_help_text()


def test_task_id_from_text_only_accepts_uuid():
    """不能把“最新的那个”整句话当任务号去查库。"""
    task_id = "40e1f9ae-a30c-46f5-93f0-562a8fb92cca"
    assert feishu_app._task_id_from_text(f"看下 {task_id} 状态") == task_id
    assert feishu_app._task_id_from_text("最新的那个") == ""


def test_task_status_text_is_short_and_has_detail_link():
    """状态回复保持短格式，不把报告正文再贴一遍。"""
    task = {
        "id": "40e1f9ae-a30c-46f5-93f0-562a8fb92cca",
        "title": "磁盘排查",
        "task_type": "investigation",
        "status": "report_ready",
        "status_text": "报告已完成",
        "submitted_by_name": "管理员",
        "assignee": {"display_name": "管理员"},
    }
    text = feishu_app._task_status_text(task)
    assert "🔵 任务状态" in text
    assert "状态：报告已完成" in text
    assert "处理人：管理员" in text
    assert "/#/tasks/40e1f9ae-a30c-46f5-93f0-562a8fb92cca" in text


def test_group_command_rejects_unbound_and_non_investigator(monkeypatch):
    """未绑定先提示绑定；普通账号重跑排查要拒绝。"""
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def transaction(self):
            return self

    monkeypatch.setattr(
        feishu_app, "_load_raw",
        lambda settings: ({"chat_ids": ["oc_group"]}, True),
    )
    monkeypatch.setattr(feishu_app, "connect_app", lambda settings: _Conn())
    monkeypatch.setattr(feishu_app, "_record_event", lambda *args, **kwargs: 1)
    monkeypatch.setattr(feishu_app, "_record_result", lambda *args, **kwargs: None)

    base = dict(
        event_id="evt_1", open_id="ou_user", chat_id="oc_group",
        chat_type="group", bot_open_id="ou_bot",
    )

    monkeypatch.setattr(feishu_app, "_resolve_identity", lambda conn, open_id: None)
    result = feishu_app.handle_group_message(
        None, text="/状态", mentioned_open_ids=["ou_bot"], **base,
    )
    assert result["handled"] is True
    assert "还没绑定平台账号" in result["reply"]

    monkeypatch.setattr(
        feishu_app, "_resolve_identity",
        lambda conn, open_id: {
            "user_id": 2, "username": "viewer_only",
            "display_name": "只读账号", "roles": ["viewer"],
        },
    )
    result = feishu_app.handle_group_message(
        None,
        text="/重跑排查 40e1f9ae-a30c-46f5-93f0-562a8fb92cca",
        mentioned_open_ids=["ou_bot"], **base,
    )
    assert result["handled"] is True
    assert "没有提交排查任务的权限" in result["reply"]


def test_group_rerun_submits_directly_without_confirmation(monkeypatch):
    """取消二次确认：/重跑排查 直接提交新任务，并记录新任务号。"""
    from app.services import investigation_service

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def transaction(self):
            return self

        def execute(self, sql, params):
            self._row = ("磁盘排查", "检查根分区", {}) if "SELECT title" in sql else None
            return self

        def fetchone(self):
            return self._row

    captured = {}

    def fake_record(settings, event_id, result, detail, actor_id=None, task_id=""):
        captured.update(result=result, detail=detail, task_id=task_id)

    def fake_submit(*args, **kwargs):
        return {"id": "new-task-id"}

    monkeypatch.setattr(
        feishu_app, "_load_raw",
        lambda settings: ({"chat_ids": ["oc_group"]}, True),
    )
    monkeypatch.setattr(feishu_app, "connect_app", lambda settings: _Conn())
    monkeypatch.setattr(feishu_app, "_record_event", lambda *args, **kwargs: 1)
    monkeypatch.setattr(feishu_app, "_record_result", fake_record)
    monkeypatch.setattr(feishu_app, "_rerun_problem", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        feishu_app, "_resolve_identity",
        lambda conn, open_id: {
            "user_id": 1, "username": "admin",
            "display_name": "管理员", "roles": ["admin"],
        },
    )
    monkeypatch.setattr(investigation_service, "submit_task", fake_submit)

    result = feishu_app.handle_group_message(
        None,
        event_id="evt_rerun",
        open_id="ou_admin",
        chat_id="oc_group",
        chat_type="group",
        text="/重跑排查 40e1f9ae-a30c-46f5-93f0-562a8fb92cca",
        mentioned_open_ids=["ou_bot"],
        bot_open_id="ou_bot",
    )

    assert result["handled"] is True
    assert "已重新提交排查" in result["reply"]
    assert "new-task-id" in result["reply"]
    assert "按实际 token 计费" in result["reply"]
    assert captured["result"] == "rerun_submitted"
    assert captured["task_id"] == "40e1f9ae-a30c-46f5-93f0-562a8fb92cca"


def test_report_digest_contains_conclusion_root_cause_and_solution():
    """排查通知必须带结论、根因和处置方案，而不是只说报告已完成。"""
    report = {
        "headline": "磁盘使用率 91%，/var 日志占满",
        "root_cause": "日志轮转失效，旧日志持续增长。",
        "solution": "1. 清理旧日志\n2. 修复 logrotate 配置",
        "conclusions": [{"text": "备用结论"}],
    }
    text = notifications._report_digest(report)
    assert "结论：磁盘使用率 91%" in text
    assert "根因：日志轮转失效" in text
    assert "处置方案：" in text
    assert "修复 logrotate" in text


def test_report_digest_turns_literal_newlines_into_real_lines():
    """模型写成字面量 `\\n` 时，飞书正文要显示成真实换行，不能带反斜杠。"""
    report = {
        "headline": "取数失败",
        "root_cause": "1. 直接原因：SSH 不通\\n2. 触发链：Prometheus 无数据",
        "solution": "```bash\\nssh <host> df -h /\\n```\\n以上只读。",
    }
    text = notifications._report_digest(report)
    assert "\\n" not in text
    assert "```" not in text
    assert "根因：直接原因：SSH 不通" in text
    assert "触发链" not in text
    assert "处置方案：ssh <host> df -h /\n以上只读。" in text


def test_plan_commands_only_returns_apply_commands():
    """部署通知只带 apply 命令，不带目标账号、密码等敏感字段。"""
    plan = {
        "target": {"user": "root", "password_enc": "secret"},
        "operations": [
            {"title": "安装 Nginx", "command": "yum install -y nginx", "scope": "dangerous"},
            {"title": "启动 Nginx", "argv": ["systemctl", "start", "nginx"], "scope": "apply"},
        ],
    }
    text = notifications._plan_commands(plan)
    assert "1. 安装 Nginx：yum install -y nginx（高危）" in text
    assert "2. 启动 Nginx：systemctl start nginx" in text
    assert "root" not in text
    assert "secret" not in text


def test_oncall_scope_names_are_explicit():
    """值班类型必须能区分审批和告警，避免前端只看到英文 scope。"""
    assert oncall.SCOPE_NAMES["approval"] == "审批值班"
    assert oncall.SCOPE_NAMES["alert"] == "告警值班"
