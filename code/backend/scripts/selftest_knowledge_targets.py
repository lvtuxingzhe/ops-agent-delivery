"""A2 结构化目标字段真库自检：创建、精确过滤、正文回退、清理。"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import load_dotenv, load_settings  # noqa: E402
from app.services import knowledge_base_service as kb  # noqa: E402

ADMIN = {"id": 1, "roles": ["admin"]}


def _title(prefix: str) -> str:
    return f"A2 结构化自检-{prefix}-{uuid.uuid4().hex[:8]}"


def main() -> int:
    load_dotenv()
    settings = load_settings()
    created: list[str] = []
    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name} {detail}")

    try:
        exact = kb.create_manual(settings, ADMIN, {
            "title": _title("exact"),
            "summary": "A2 精确主机过滤",
            "body": "目标 10.0.0.1，服务 nginx。",
            "tech_domain": "middleware",
            "target_hosts": ["10.0.0.1"],
            "target_services": ["nginx"],
        })
        created.append(exact["id"])
        near = kb.create_manual(settings, ADMIN, {
            "title": _title("near"),
            "summary": "A2 相近主机过滤",
            "body": "正文里也写了 10.0.0.1，但结构化目标是 10.0.0.10。",
            "tech_domain": "middleware",
            "target_hosts": ["10.0.0.10"],
            "target_services": ["nginx-ingress"],
        })
        created.append(near["id"])
        fallback = kb.create_manual(settings, ADMIN, {
            "title": _title("fallback"),
            "summary": "A2 未标注回退",
            "body": "旧知识没有目标字段，正文出现 10.0.0.1。",
            "tech_domain": "middleware",
        })
        created.append(fallback["id"])

        check("create returns target_hosts", exact.get("target_hosts") == ["10.0.0.1"])
        check("create returns target_services", exact.get("target_services") == ["nginx"])

        host_hit = kb.rag_search(
            settings, ADMIN, exact["title"], limit=5, kind="manual",
            host="10.0.0.1",
        )
        ids = [item["id"] for item in host_hit["documents"]]
        check("host 10.0.0.1 includes exact", exact["id"] in ids, str(ids))
        check("host 10.0.0.1 excludes 10.0.0.10", near["id"] not in ids, str(ids))
        check("host fallback includes unmarked", fallback["id"] in ids, str(ids))

        service_hit = kb.rag_search(
            settings, ADMIN, exact["title"], limit=5, kind="manual",
            service="nginx",
        )
        service_ids = [item["id"] for item in service_hit["documents"]]
        check("service nginx includes exact", exact["id"] in service_ids, str(service_ids))
        check("service nginx excludes nginx-ingress", near["id"] not in service_ids, str(service_ids))

        detail = kb.get_entry(settings, ADMIN, exact["id"])
        check("detail returns target_hosts", detail.get("target_hosts") == ["10.0.0.1"])
        check("detail returns target_services", detail.get("target_services") == ["nginx"])
    finally:
        for entry_id in created:
            try:
                kb.delete_manual(settings, ADMIN, entry_id.removeprefix("manual-"))
            except Exception as exc:  # noqa: BLE001 - 清理失败要让人看见
                print(f"CLEANUP FAIL {entry_id}: {exc}")
                failed += 1
        print(f"RESULT {passed} pass / {failed} fail")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
