"""逐条报告反馈：目标定位、纠正校验和报告指纹。"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from app.services import report_feedback

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, row):
        """执行 FakeCursor。"""
        self._row = row

    def fetchone(self):
        """执行 fetchone。"""
        return self._row


class FakeConn:
    def __init__(self, row):
        """执行 FakeConn。"""
        self._row = row
        self.sql = ""
        self.params = ()

    def __enter__(self):
        """执行 __enter__。"""
        return self

    def __exit__(self, exc_type, exc, tb):
        """执行 __exit__。"""
        return False

    def execute(self, sql, params=()):
        """执行 execute。"""
        self.sql = sql
        self.params = params
        return FakeCursor(self._row)


def _row(**overrides):
    """构造一条反馈数据库行，测试可按需覆盖字段。"""
    values = {
        "id": "f-1",
        "task_id": "t-1",
        "target_type": "root_cause",
        "target_index": 0,
        "target_text": "磁盘写满",
        "report_hash": "h" * 32,
        "verdict": "reject",
        "corrected_text": "",
        "note": "日志显示是 inode 满",
        "created_by": 7,
        "created_by_name": "老王",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return (
        values["id"], values["task_id"], values["target_type"], values["target_index"],
        values["target_text"], values["report_hash"], values["verdict"],
        values["corrected_text"], values["note"], values["created_by"],
        values["created_by_name"], values["created_at"], values["updated_at"],
    )


@pytest.fixture
def report():
    """一份最小报告：根因和两条结论。"""
    return {
        "root_cause": "磁盘写满",
        "conclusions": [
            {"text": "根分区使用率 100%"},
            {"text": "日志增长过快"},
        ],
    }


def test_report_hash_is_stable():
    """同样内容摘要一致，内容变化摘要变化。"""
    first = report_feedback.report_hash({"root_cause": "磁盘写满"})
    second = report_feedback.report_hash({"root_cause": "磁盘写满"})
    assert first == second
    assert first != report_feedback.report_hash({"root_cause": "内存不足"})


def test_submit_root_cause_uses_report_text(monkeypatch, report):
    """根因反馈从服务端报告取快照，不信任前端文字。"""
    conn = FakeConn(_row())
    monkeypatch.setattr(report_feedback, "connect_app", lambda _settings: conn)
    item = report_feedback.submit_feedback(
        object(), "t-1", {"id": 7, "display_name": "老王"}, report,
        target_type="root_cause", target_index=0, verdict="reject",
        note="日志显示是 inode 满",
    )
    assert item["target_text"] == "磁盘写满"
    assert item["target_index"] == 0
    params = conn.params
    assert params[2] == "root_cause"
    assert params[3] == 0
    assert params[5] == report_feedback.report_hash(report)
    assert params[6] == "reject"


def test_submit_conclusion_uses_index(monkeypatch, report):
    """结论按 conclusions 下标取快照。"""
    conn = FakeConn(_row(target_type="conclusion", target_index=1,
                         target_text="日志增长过快"))
    monkeypatch.setattr(report_feedback, "connect_app", lambda _settings: conn)
    item = report_feedback.submit_feedback(
        object(), "t-1", {"id": 7, "username": "laowang"}, report,
        target_type="conclusion", target_index=1, verdict="confirm",
    )
    assert item["target_index"] == 1
    assert item["target_text"] == "日志增长过快"
    assert conn.params[4] == "日志增长过快"


def test_correct_requires_text(report):
    """选择纠正却不填内容要直接拒绝。"""
    with pytest.raises(report_feedback.FeedbackError):
        report_feedback.submit_feedback(
            object(), "t-1", {"id": 7}, report,
            target_type="root_cause", target_index=0,
            verdict="correct", corrected_text="   ",
        )


def test_conclusion_index_must_exist(report):
    """报告更新后旧序号不能继续提交。"""
    with pytest.raises(report_feedback.FeedbackError):
        report_feedback.submit_feedback(
            object(), "t-1", {"id": 7}, report,
            target_type="conclusion", target_index=2, verdict="confirm",
        )


def test_unknown_target_is_rejected(report):
    """方案暂不支持逐条反馈，避免把不存在的位置写进库。"""
    with pytest.raises(report_feedback.FeedbackError):
        report_feedback.submit_feedback(
            object(), "t-1", {"id": 7}, report,
            target_type="solution", target_index=0, verdict="confirm",
        )


def test_feedback_notes_shape():
    """反馈要压成可读复核条目，纠正内容和备注都要保留。"""
    notes = report_feedback.feedback_notes([
        {
            "target_type": "root_cause", "target_index": 0, "verdict": "correct",
            "created_by_name": "老王", "corrected_text": "inode 用完",
            "note": "df -i 已确认",
        },
        {
            "target_type": "conclusion", "target_index": 1, "verdict": "reject",
            "created_by_name": "小李", "corrected_text": "", "note": "",
        },
    ])
    assert notes[0] == "根因：已纠正（老王），正确内容：inode 用完，备注：df -i 已确认"
    assert notes[1] == "结论 2：已标为不准确（小李）"


def test_feedback_notes_truncates_long_list():
    """复核记录过多时只展开前 8 条，并明确告知还有多少条。"""
    feedback = [
        {
            "target_type": "conclusion", "target_index": index, "verdict": "confirm",
            "created_by_name": "管理员", "corrected_text": "", "note": "",
        }
        for index in range(10)
    ]
    notes = report_feedback.feedback_notes(feedback)
    assert len(notes) == 9
    assert notes[-1] == "另有 2 条复核记录未展开"
