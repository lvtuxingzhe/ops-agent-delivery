"""命令输出分行：进度条里的 `\r` 也要及时回调。"""

from __future__ import annotations

from tools.stream_lines import split_stream_chunk


def test_split_stream_chunk_keeps_partial_line():
    lines, buffer = split_stream_chunk("", "10%\r20%")
    assert lines == ["10%"]
    assert buffer == "20%"


def test_split_stream_chunk_handles_crlf_and_progress_updates():
    lines, buffer = split_stream_chunk("20%", "\r30%\n完成\r\n")
    assert lines == ["20%", "30%", "完成"]
    assert buffer == ""


def test_split_stream_chunk_does_not_lose_text_across_chunks():
    lines, buffer = split_stream_chunk("正在安", "装\n下一步")
    assert lines == ["正在安装"]
    assert buffer == "下一步"
