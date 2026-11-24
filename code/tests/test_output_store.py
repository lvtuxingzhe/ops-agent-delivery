"""长输出通用存储测试：预览、按行读取、搜索、句柄隔离。"""

from __future__ import annotations

import json
import os
import time

import pytest
from ops_guard import RedactionSession
from tools.output_store import OutputStore, OutputStoreError
from tools.output_tools import OutputReadTools


def _lines(count: int = 1200) -> str:
    return "\n".join(f"line-{index:04d}: keepalived detail" for index in range(count))


def test_log_preview_keeps_head_and_tail(tmp_path):
    store = OutputStore(task_id="preview-log", root=tmp_path)
    text = _lines()
    preview = store.preview(text, kind="log", max_chars=300)

    assert preview.truncated is True
    assert preview.text.startswith("line-0000")
    assert "line-1199: keepalived detail" in preview.text
    assert "省略第" in preview.text
    assert preview.omitted_lines > 0
    assert preview.omitted_start > 1
    assert preview.omitted_end < preview.total_lines
    assert preview.omitted_lines == preview.omitted_end - preview.omitted_start + 1


def test_config_preview_keeps_more_head(tmp_path):
    store = OutputStore(task_id="preview-config", root=tmp_path)
    text = "\n".join(
        [f"setting-{index}=value" for index in range(200)]
        + [f"log-line-{index}" for index in range(200)]
    )
    preview = store.preview(text, kind="config", max_chars=400)

    assert preview.truncated is True
    assert "setting-0=value" in preview.text
    assert "log-line-199" in preview.text
    assert preview.text.index("setting-0=value") < preview.text.index("省略第")


def test_tail_only_preview_drops_head(tmp_path):
    store = OutputStore(task_id="preview-tail", root=tmp_path)
    preview = store.preview(_lines(), kind="log", max_chars=200, tail_only=True)

    assert preview.truncated is True
    assert preview.text.startswith("…（省略第 1-")
    assert "line-1199: keepalived detail" in preview.text


def test_save_read_and_grep(tmp_path):
    store = OutputStore(task_id="read-grep", root=tmp_path)
    text = _lines()
    saved = store.save(text, kind="log", metadata={"command": "journalctl"})

    start = store.read(saved.handle, start_line=500, max_lines=20)
    assert start["ok"] is True
    assert start["start_line"] == 500
    assert start["content"].startswith("line-0499")
    assert start["next_start_line"] == 520

    found = store.grep(saved.handle, "line-0999", context=1)
    assert found["ok"] is True
    assert found["total_matches"] == 1
    assert found["matches"][0]["line"] == 1000
    assert found["matches"][0]["before"] == ["line-0998: keepalived detail"]
    assert found["matches"][0]["after"] == ["line-1000: keepalived detail"]


def test_handle_is_opaque_and_task_scoped(tmp_path):
    store = OutputStore(task_id="task-a", root=tmp_path)
    saved = store.save("hello", kind="text")

    other = OutputStore(task_id="task-b", root=tmp_path)
    with pytest.raises(OutputStoreError):
        other.load(saved.handle)
    with pytest.raises(OutputStoreError):
        store.load("../../etc/passwd")
    with pytest.raises(OutputStoreError):
        store.load("out_not-a-handle")


def test_output_tools_return_json_and_reject_missing(tmp_path):
    store = OutputStore(task_id="tools", root=tmp_path)
    saved = store.save(_lines(), kind="log")
    tools = OutputReadTools(store)

    read = json.loads(tools.read_output(saved.handle, start_line=1, max_lines=3))
    assert read["ok"] is True
    assert read["content"].startswith("line-0000")
    assert read["next_start_line"] == 4

    grep = json.loads(tools.grep_output(saved.handle, "line-1199"))
    assert grep["ok"] is True
    assert grep["total_matches"] == 1

    missing = json.loads(tools.read_output("out_00000000000000000000000000000000"))
    assert missing["ok"] is False
    assert missing["reason"] == "output_not_found"


def test_prune_removes_old_outputs(tmp_path):
    store = OutputStore(task_id="prune", root=tmp_path, ttl_seconds=3600)
    saved = store.save("old output", kind="text")
    path = store.task_dir / f"{saved.handle}.txt"
    old = time.time() - 7200
    os.utime(path, (old, old))

    store.prune()
    assert path.exists() is False


def test_output_is_redacted_before_persist(tmp_path):
    store = OutputStore(
        task_id="redact", root=tmp_path,
        session=RedactionSession(task_id="redact"),
    )
    saved = store.save("password=SuperSecret123\nhost=10.0.0.1", kind="text")

    assert "SuperSecret123" not in saved.text
    assert "10.0.0.1" not in saved.text
    assert store.load(saved.handle) == saved.text
