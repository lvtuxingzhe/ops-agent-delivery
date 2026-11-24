"""工具长输出的存储与截断。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ops_guard import redact_text

CODE_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_ROOT_ENV = os.environ.get("TOOL_OUTPUT_ROOT", "").strip()
DEFAULT_OUTPUT_ROOT = (
    Path(_OUTPUT_ROOT_ENV) if _OUTPUT_ROOT_ENV
    else CODE_ROOT / "var" / "tmp" / "tool-output"
)
HANDLE_RE = re.compile(r"^out_[a-f0-9]{32}$")
MAX_PREVIEW_CHARS = 4000
MAX_READ_CHARS = 4000
MAX_READ_LINES = 500
MAX_GREP_MATCHES = 50
MAX_PATTERN_CHARS = 200
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60

# 只影响预览分配，不影响全文保存。
_HEAD_RATIO = {
    "log": 0.2,
    "config": 0.7,
    "text": 0.5,
    "structured": 0.5,
}


class OutputStoreError(Exception):
    """输出文件读写失败；调用方应退回短快照，而不是让取证失败。"""


@dataclass(frozen=True)
class SavedOutput:
    """一次落盘结果。stdout/stderr 是脱敏后的文本，可直接做预览。"""

    handle: str
    text: str
    stdout: str
    stderr: str
    total_chars: int
    total_lines: int


@dataclass(frozen=True)
class Preview:
    """给模型看的预览；行号用于让模型继续读取被省略的中间段。"""

    text: str
    total_chars: int
    total_lines: int
    returned_lines: int
    omitted_lines: int
    omitted_start: int
    omitted_end: int
    truncated: bool
    kind: str


def _task_key(task_id: str) -> str:
    """任务目录只暴露哈希，不让任务号出现在文件路径里。"""
    return hashlib.sha256((task_id or "unknown").encode()).hexdigest()[:24]


def _clip_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


class OutputStore:
    """一次任务一个实例；handle 只在本任务目录内有效。"""

    def __init__(
        self,
        *,
        task_id: str = "",
        session: Any = None,
        root: Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.task_id = str(task_id or "")
        self.session = session
        self.root = Path(root) if root is not None else DEFAULT_OUTPUT_ROOT
        self.ttl_seconds = max(0, int(ttl_seconds))

    @property
    def task_dir(self) -> Path:
        return self.root / _task_key(self.task_id)

    def _redact(self, text: str) -> str:
        if self.session is None:
            return text
        redacted, _, _ = redact_text(text, self.session)
        return redacted

    def save(self, text: str, *, kind: str = "text",
             metadata: dict[str, Any] | None = None) -> SavedOutput:
        """保存单段文本。"""
        return self.save_parts(text, "", kind=kind, metadata=metadata)

    def save_parts(
        self,
        stdout: str,
        stderr: str,
        *,
        kind: str = "text",
        metadata: dict[str, Any] | None = None,
    ) -> SavedOutput:
        """保存 stdout/stderr；落盘内容是脱敏后的合并文本。"""
        stdout_redacted = self._redact(str(stdout or ""))
        stderr_redacted = self._redact(str(stderr or ""))
        text = stdout_redacted
        if stderr_redacted:
            text = text.rstrip("\n") + "\n=== STDERR ===\n" + stderr_redacted

        handle = "out_" + secrets.token_hex(16)
        folder = self.task_dir
        text_path = folder / f"{handle}.txt"
        meta_path = folder / f"{handle}.json"
        meta = {
            "handle": handle,
            "kind": str(kind or "text"),
            "created_at": int(time.time()),
            "total_chars": len(text),
            "stdout_chars": len(stdout_redacted),
            "stderr_chars": len(stderr_redacted),
            "metadata": metadata or {},
        }
        try:
            folder.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text, encoding="utf-8")
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            for path in (text_path, meta_path):
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
        except OSError as exc:
            raise OutputStoreError("输出文件写入失败") from exc
        self.prune()
        return SavedOutput(
            handle=handle,
            text=text,
            stdout=stdout_redacted,
            stderr=stderr_redacted,
            total_chars=len(text),
            total_lines=len(text.splitlines()),
        )

    def load(self, handle: str) -> str:
        """按 handle 读全文；handle 不合法或跨任务都当作不存在。"""
        if not HANDLE_RE.match(str(handle or "")):
            raise OutputStoreError("输出句柄不合法")
        path = self.task_dir / f"{handle}.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise OutputStoreError("输出文件不存在") from exc

    def preview(self, text: str, *, kind: str = "text",
                max_chars: int = MAX_PREVIEW_CHARS,
                tail_only: bool = False) -> Preview:
        """按 kind 保留头尾，裁掉中间；行太长时退回字符切。"""
        text = str(text or "")
        total_chars = len(text)
        lines = text.splitlines()
        total_lines = len(lines)
        limit = max(1, int(max_chars))
        if total_chars <= limit:
            return Preview(
                text=text,
                total_chars=total_chars,
                total_lines=total_lines,
                returned_lines=total_lines,
                omitted_lines=0,
                omitted_start=0,
                omitted_end=0,
                truncated=False,
                kind=str(kind or "text"),
            )

        if tail_only:
            tail = self._tail_lines(lines, limit)
            returned = len(tail.lines)
            omitted = total_lines - returned
            if not tail.lines:
                return self._char_preview(
                    text, kind=kind, head_chars=0, tail_chars=limit,
                    total_lines=total_lines, omitted_lines=0,
                )
            body = tail.text
            marker = (
                f"…（省略第 1-{omitted} 行，共 {total_lines} 行）…\n"
                if omitted > 0 else ""
            )
            return Preview(
                text=marker + body,
                total_chars=total_chars,
                total_lines=total_lines,
                returned_lines=returned,
                omitted_lines=omitted,
                omitted_start=1 if omitted > 0 else 0,
                omitted_end=omitted,
                truncated=omitted > 0,
                kind=str(kind or "text"),
            )

        ratio = _HEAD_RATIO.get(str(kind or "text"), 0.5)
        head_budget = max(1, int(limit * ratio))
        tail_budget = max(1, limit - head_budget)
        head = self._head_lines(lines, head_budget)
        tail = self._tail_lines(lines, tail_budget, skip=head.line_count)
        if not head.lines or not tail.lines:
            # 单行超长或头尾预算都被一行吃掉时，按字符保头尾。
            return self._char_preview(
                text, kind=kind, head_chars=head_budget, tail_chars=tail_budget,
                total_lines=total_lines, omitted_lines=0,
            )

        omitted = total_lines - head.line_count - tail.line_count
        if omitted <= 0:
            return Preview(
                text=text,
                total_chars=total_chars,
                total_lines=total_lines,
                returned_lines=total_lines,
                omitted_lines=0,
                omitted_start=0,
                omitted_end=0,
                truncated=False,
                kind=str(kind or "text"),
            )
        start_line = head.line_count + 1
        end_line = total_lines - tail.line_count
        marker = f"…（省略第 {start_line}-{end_line} 行，共 {total_lines} 行）…"
        body = head.text + "\n" + marker + "\n" + tail.text
        return Preview(
            text=body,
            total_chars=total_chars,
            total_lines=total_lines,
            returned_lines=head.line_count + tail.line_count,
            omitted_lines=omitted,
            omitted_start=start_line,
            omitted_end=end_line,
            truncated=True,
            kind=str(kind or "text"),
        )

    def _head_lines(self, lines: list[str], budget: int) -> _LineBlock:
        selected: list[str] = []
        used = 0
        for line in lines:
            cost = len(line) + 1
            if selected and used + cost > budget:
                break
            if not selected and cost > budget:
                # 第一行就超预算：交给字符切，避免整段预览只剩标记。
                return _LineBlock([], 0)
            selected.append(line)
            used += cost
        return _LineBlock(selected, len(selected))

    def _tail_lines(self, lines: list[str], budget: int,
                    skip: int = 0) -> _LineBlock:
        available = max(0, len(lines) - skip)
        selected: list[str] = []
        used = 0
        for line in reversed(lines[-available:] if available else []):
            cost = len(line) + 1
            if selected and used + cost > budget:
                break
            if not selected and cost > budget:
                return _LineBlock([], 0)
            selected.append(line)
            used += cost
        selected.reverse()
        return _LineBlock(selected, len(selected))

    def _char_preview(self, text: str, *, kind: str, head_chars: int,
                      tail_chars: int, total_lines: int,
                      omitted_lines: int) -> Preview:
        head = text[:max(0, head_chars)]
        tail = text[-max(0, tail_chars):] if tail_chars else ""
        marker = "\n…（省略中间段）…\n"
        body = head + marker + tail
        return Preview(
            text=body,
            total_chars=len(text),
            total_lines=total_lines,
            returned_lines=total_lines if omitted_lines == 0 else max(0, total_lines - omitted_lines),
            omitted_lines=omitted_lines,
            omitted_start=0,
            omitted_end=0,
            truncated=True,
            kind=str(kind or "text"),
        )

    def read(self, handle: str, *, start_line: int = 1,
             max_lines: int = 100, max_chars: int = MAX_READ_CHARS) -> dict[str, Any]:
        """按 1-based 行号读取；返回行范围和下一段的起始行。"""
        text = self.load(handle)
        lines = text.splitlines()
        total_lines = len(lines)
        try:
            start = max(1, int(start_line or 1))
        except (TypeError, ValueError):
            start = 1
        count = _clip_int(max_lines, 1, MAX_READ_LINES, 100)
        chars = _clip_int(max_chars, 1, MAX_READ_CHARS, MAX_READ_CHARS)
        if start > total_lines:
            return {
                "ok": True,
                "handle": handle,
                "start_line": start,
                "end_line": 0,
                "total_lines": total_lines,
                "content": "",
                "next_start_line": None,
                "eof": True,
            }
        selected: list[str] = []
        used = 0
        line_truncated = False
        for line in lines[start - 1:start - 1 + count]:
            cost = len(line) + 1
            if used + cost > chars:
                if not selected:
                    selected.append(line[:chars])
                    line_truncated = True
                break
            selected.append(line)
            used += cost
        content = "\n".join(selected)
        end_line = start + len(selected) - 1 if selected else start - 1
        next_start = end_line + 1 if end_line < total_lines else None
        return {
            "ok": True,
            "handle": handle,
            "start_line": start,
            "end_line": end_line,
            "total_lines": total_lines,
            "content": content,
            "next_start_line": next_start,
            "line_truncated": line_truncated,
            "eof": next_start is None,
        }

    def read_chars(self, handle: str, *, offset: int = 0,
                   limit: int = MAX_READ_CHARS) -> dict[str, Any]:
        """兼容旧的字符偏移读取；新工具优先用按行读取。"""
        text = self.load(handle)
        total = len(text)
        try:
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            start = 0
        size = _clip_int(limit, 1, MAX_READ_CHARS, MAX_READ_CHARS)
        content = text[start:start + size]
        next_offset = start + len(content) if start + len(content) < total else None
        return {
            "ok": True,
            "handle": handle,
            "offset": start,
            "limit": size,
            "total_chars": total,
            "content": content,
            "next_offset": next_offset,
            "eof": next_offset is None,
        }

    def grep(self, handle: str, pattern: str, *, context: int = 2,
             max_matches: int = 20) -> dict[str, Any]:
        """按行搜索；返回命中行和上下文，不返回整份文件。"""
        text = self.load(handle)
        lines = text.splitlines()
        total_lines = len(lines)
        raw_pattern = str(pattern or "")
        if not raw_pattern or len(raw_pattern) > MAX_PATTERN_CHARS:
            return {"ok": False, "reason": "invalid_pattern", "handle": handle}
        try:
            regex = re.compile(raw_pattern, re.IGNORECASE)
        except re.error:
            return {"ok": False, "reason": "invalid_pattern", "handle": handle}

        ctx = _clip_int(context, 0, 10, 2)
        limit = _clip_int(max_matches, 1, MAX_GREP_MATCHES, 20)
        matches: list[dict[str, Any]] = []
        total_matches = 0
        for index, line in enumerate(lines):
            if not regex.search(line):
                continue
            total_matches += 1
            if len(matches) >= limit:
                continue
            matches.append({
                "line": index + 1,
                "text": line[:MAX_READ_CHARS],
                "before": lines[max(0, index - ctx):index],
                "after": lines[index + 1:index + 1 + ctx],
            })
        return {
            "ok": True,
            "handle": handle,
            "pattern": raw_pattern,
            "total_lines": total_lines,
            "total_matches": total_matches,
            "returned_matches": len(matches),
            "context": ctx,
            "matches": matches,
        }

    def prune(self) -> None:
        """删除超过 TTL 的输出文件；失败不影响取证。"""
        if self.ttl_seconds <= 0:
            return
        cutoff = time.time() - self.ttl_seconds
        folder = self.task_dir
        if not folder.is_dir():
            return
        try:
            for path in folder.iterdir():
                if not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass


@dataclass(frozen=True)
class _LineBlock:
    """内部行块：text 是给模型的正文，line_count 用于算省略范围。"""

    lines: list[str]
    line_count: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


__all__ = [
    "DEFAULT_OUTPUT_ROOT", "DEFAULT_TTL_SECONDS", "OutputStore",
    "OutputStoreError", "Preview", "SavedOutput",
]
