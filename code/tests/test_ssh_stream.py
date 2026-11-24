"""A2 远程行级日志的读取逻辑测试：用假 channel，不连 SSH。"""

from __future__ import annotations

from worker.tools.ssh_exec import _read_streaming


class _Channel:
    def __init__(self, out: list[bytes], err: list[bytes]) -> None:
        self.out = list(out)
        self.err = list(err)
        self.closed = False

    def settimeout(self, _seconds: float) -> None:
        pass

    def recv_ready(self) -> bool:
        return bool(self.out)

    def recv(self, _size: int) -> bytes:
        return self.out.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self.err)

    def recv_stderr(self, _size: int) -> bytes:
        return self.err.pop(0)

    def exit_status_ready(self) -> bool:
        return not self.out and not self.err

    def recv_exit_status(self) -> int:
        return 0

    def close(self) -> None:
        self.closed = True


class _Stream:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel


def test_read_streaming_splits_lines_and_flushes_tail():
    channel = _Channel([b"line1\nline2\npar", b"tial"], [b"err1\n"])
    lines: list[tuple[str, str]] = []
    out, err, code, timed_out, truncated = _read_streaming(
        _Stream(channel), _Stream(channel), timeout_s=5,
        on_line=lambda name, text: lines.append((name, text)),
    )
    assert code == 0
    assert not timed_out and not truncated
    assert (out, err) == ("line1\nline2\npartial", "err1\n")
    assert lines == [("stdout", "line1"), ("stdout", "line2"),
                     ("stderr", "err1"), ("stdout", "partial")]
