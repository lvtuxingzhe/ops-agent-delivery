"""A2 结构化目标字段 HTTP 验收：真实登录、创建、列表、RAG 过滤、清理。"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402

BASE = "http://127.0.0.1:8000/api"
PASSED = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def call(method: str, path: str, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Client-Kind": "web",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body_value = json.loads(raw)
        except Exception:
            body_value = {"raw": raw}
        return exc.code, body_value


def main() -> int:
    load_dotenv()
    settings = load_settings()
    login_status, login_body = call("POST", "/auth/login", body={
        "method": "password",
        "username": settings.admin_username,
        "password": settings.admin_password,
    })
    check("管理员登录", login_status == 200, f"HTTP {login_status}")
    if login_status != 200:
        print(f"RESULT {PASSED} pass / {FAILED} fail")
        return 1
    token = login_body["token"]

    created: list[str] = []
    suffix = uuid.uuid4().hex[:8]
    try:
        exact = call("POST", "/knowledge", token, {
            "title": f"A2 HTTP 精确-{suffix}",
            "summary": "A2 精确主机过滤",
            "body": "目标 192.0.2.1，服务 nginx。",
            "tech_domain": "middleware",
            "target_hosts": ["192.0.2.1"],
            "target_services": ["nginx"],
        })
        check("创建精确条目", exact[0] == 201, f"HTTP {exact[0]}")
        if exact[0] != 201:
            return 1
        exact_id = exact[1]["id"]
        created.append(exact_id)

        near = call("POST", "/knowledge", token, {
            "title": f"A2 HTTP 相近-{suffix}",
            "summary": "A2 相近主机过滤",
            "body": "正文也写 192.0.2.1，但目标是 192.0.2.10。",
            "tech_domain": "middleware",
            "target_hosts": ["192.0.2.10"],
            "target_services": ["nginx-ingress"],
        })
        check("创建相近条目", near[0] == 201, f"HTTP {near[0]}")
        near_id = near[1]["id"]
        created.append(near_id)

        fallback = call("POST", "/knowledge", token, {
            "title": f"A2 HTTP 回退-{suffix}",
            "summary": "A2 未标注回退",
            "body": "旧知识没有目标字段，正文出现 192.0.2.1。",
            "tech_domain": "middleware",
        })
        check("创建未标注条目", fallback[0] == 201, f"HTTP {fallback[0]}")
        fallback_id = fallback[1]["id"]
        created.append(fallback_id)

        detail = call("GET", f"/knowledge/{exact_id}", token)
        check("详情返回 target_hosts", detail[1].get("target_hosts") == ["192.0.2.1"])
        check("详情返回 target_services", detail[1].get("target_services") == ["nginx"])

        listing = call("GET", "/knowledge?limit=50", token)
        ids = {item["id"]: item for item in listing[1].get("items", [])}
        check("列表返回 target_hosts", ids.get(exact_id, {}).get("target_hosts") == ["192.0.2.1"])

        rag = call("POST", "/knowledge/rag", token, {
            "query": f"A2 HTTP 精确-{suffix}",
            "limit": 5,
            "kind": "manual",
            "host": "192.0.2.1",
        })
        rag_ids = [item["id"] for item in rag[1].get("documents", [])]
        check("RAG host 包含精确条目", exact_id in rag_ids, str(rag_ids))
        check("RAG host 排除 192.0.2.10", near_id not in rag_ids, str(rag_ids))
        check("RAG host 包含未标注回退", fallback_id in rag_ids, str(rag_ids))

        rag_service = call("POST", "/knowledge/rag", token, {
            "query": f"A2 HTTP 精确-{suffix}",
            "limit": 5,
            "kind": "manual",
            "service": "nginx",
        })
        service_ids = [item["id"] for item in rag_service[1].get("documents", [])]
        check("RAG service 包含精确条目", exact_id in service_ids, str(service_ids))
        check("RAG service 排除 nginx-ingress", near_id not in service_ids, str(service_ids))

        invalid = call("POST", "/knowledge", token, {
            "title": f"A2 HTTP 非法-{suffix}",
            "target_hosts": "192.0.2.1",
        })
        check("非法 target_hosts 返回 400", invalid[0] == 400, f"HTTP {invalid[0]}")
        detail_value = invalid[1].get("detail")
        code = ""
        if isinstance(detail_value, dict):
            code = str(detail_value.get("code") or "")
        elif isinstance(detail_value, str):
            code = detail_value.split("：", 1)[0]
        check("非法 target_hosts 错误码", code == "target_hosts_invalid", str(code))
    finally:
        for entry_id in created:
            status, _ = call("DELETE", f"/knowledge/{entry_id}", token)
            if status != 200:
                check(f"清理 {entry_id}", False, f"HTTP {status}")
    print(f"RESULT {PASSED} pass / {FAILED} fail")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
