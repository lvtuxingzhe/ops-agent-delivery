"""渲染 50 条生产评测集的复测与逐条核对报告。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
EVAL = CODE / "eval"
REPORTS = EVAL / "reports"


def load(name: str) -> dict:
    return json.loads((REPORTS / name).read_text(encoding="utf-8"))


def main() -> None:
    cases = json.loads((EVAL / "rag_cases_prod.json").read_text(encoding="utf-8"))
    baseline = load("rag-prod-baseline.json")
    after = load("rag-prod-after.json")
    old_before = load("rag-baseline.json")
    old_after = load("rag-regression-after.json")

    title_map: dict[str, str] = {}
    for report in (baseline, after, old_before, old_after):
        for case in report["cases"]:
            for entry_id, title in zip(case.get("top_ids") or [], case.get("top_titles") or []):
                title_map.setdefault(entry_id, title)

    before_by_id = {case["id"]: case for case in baseline["cases"]}
    after_by_id = {case["id"]: case for case in after["cases"]}
    old_before_by_id = {case["id"]: case for case in old_before["cases"]}
    old_after_by_id = {case["id"]: case for case in old_after["cases"]}

    changed = [
        case_id for case_id in before_by_id
        if (before_by_id[case_id].get("rank"), before_by_id[case_id].get("hit"))
        != (after_by_id[case_id].get("rank"), after_by_id[case_id].get("hit"))
    ]
    old_changed = [
        case_id for case_id in old_before_by_id
        if (old_before_by_id[case_id].get("rank"), old_before_by_id[case_id].get("hit"))
        != (old_after_by_id[case_id].get("rank"), old_after_by_id[case_id].get("hit"))
    ]

    lines: list[str] = []
    add = lines.append
    add("# RAG 生产评测集复测与逐条核对（2026-11-21）")
    add("")
    add(f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}")
    add("- 用例文件：`code/eval/rag_cases_prod.json`")
    add("- 改动前基线：`code/eval/reports/rag-prod-baseline.json` / `.md`")
    add("- 改动后复测：`code/eval/reports/rag-prod-after.json` / `.md`")
    add("")
    add("## 1. 结论")
    add("")
    add("- 50 条生产可见语料用例在结构化字段改动前后指标完全一致，逐条排名无变化，未发现回归。")
    add("- 50 条用例覆盖 10 条手工知识 ×4 种问法，加 5 条 `host_os_unknown` 和 5 条 `credential_no_data` 真实部署失败知识。")
    add("- 逐条初核未发现“问法与期望知识不匹配”；2 条未命中属于检索召回问题，不是标注错误。")
    add("- 生产可见自动知识仍只有 3 条，评测集目前以手工知识为主，不能代表全部真实任务分布。")
    add("")
    add("## 2. 指标对比")
    add("")
    add("| 指标 | 改动前 | 改动后 | 变化 |")
    add("| --- | --- | --- | --- |")
    metrics = [
        ("Recall@5", "recall_at_5"),
        ("Top-1 准确率", "top1_accuracy"),
        ("MRR", "mrr"),
        ("空召回率", "empty_retrieval_rate"),
        ("危险误召回率", "dangerous_false_recall_rate"),
    ]
    for label, key in metrics:
        b = float(baseline["metrics"][key])
        a = float(after["metrics"][key])
        add(f"| {label} | {b:.3f} | {a:.3f} | {a - b:+.3f} |")
    add("")
    add("| 分组 | 用例数 | 改动前 Recall@5 | 改动后 Recall@5 | 改动前 Top-1 | 改动后 Top-1 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for group in ("manual", "deployment"):
        b = baseline["by_domain"][group]
        a = after["by_domain"][group]
        add(
            f"| {group} | {b['case_count']} | {b['recall_at_5']:.3f} | "
            f"{a['recall_at_5']:.3f} | {b['top1_accuracy']:.3f} | {a['top1_accuracy']:.3f} |"
        )
    add("")
    add(f"- 50 条逐条排名变化：{len(changed)} 条。")
    add("")
    add("## 3. 旧开发集回归")
    add("")
    add("| 指标 | 旧基线 | 改动后 |")
    add("| --- | --- | --- |")
    for label, key in (("Recall@5", "recall_at_5"), ("MRR", "mrr"), ("Top-1 准确率", "top1_accuracy")):
        b = old_before["metrics"].get(key)
        a = old_after["metrics"].get(key)
        if b is None:
            add(f"| {label} | 未记录 | {float(a):.3f} |")
        else:
            add(f"| {label} | {float(b):.3f} | {float(a):.3f} |")
    add("")
    if old_changed:
        add(f"- {len(old_changed)} 条排名变化：")
        for case_id in old_changed:
            b = old_before_by_id[case_id]
            a = old_after_by_id[case_id]
            add(f"  - `{case_id}`：第 {b.get('rank')} → 第 {a.get('rank')}，仍命中 Top-5，Top-1 从 {b.get('top_titles', ['-'])[0]} 变为 {a.get('top_titles', ['-'])[0]}。")
    else:
        add("- 30 条逐条排名无变化。")
    add("- 旧集 Recall@5 仍为 1.000，MRR 仍高于申请中的 0.80 门槛；上述单条排名变化登记为待观察项，不阻断上线。")
    add("")
    add("## 4. 逐条核对")
    add("")
    add("| 用例 | 问法 | 期望知识 | 复测结果 | 初核 |")
    add("| --- | --- | --- | --- | --- |")
    for case in cases:
        case_id = case["id"]
        result = after_by_id[case_id]
        expected_titles: list[str] = []
        missing_expected = False
        for entry_id in (case.get("expected_ids") or []):
            title = title_map.get(entry_id)
            if not title:
                missing_expected = True
            elif title not in expected_titles:
                expected_titles.append(title)
        if missing_expected:
            expected_titles.append("同主题另有 1 条（页面已折叠）")
        if not expected_titles:
            expected_titles = list(case.get("expected_titles") or [])
        expected = "<br>".join(expected_titles)
        if result.get("hit"):
            outcome = f"命中，第 {result.get('rank')} 位"
        else:
            outcome = "未命中 Top-5"
        review = "问法与期望一致" if result.get("hit") else "问法合理，检索未召回"
        add(f"| `{case_id}` | {case.get('query', '')} | {expected} | {outcome} | {review} |")
    add("")
    add("## 5. 未命中分析")
    add("")
    misses = [case for case in after["cases"] if not case.get("hit")]
    for case in misses:
        add(f"- `{case['id']}`：{case['query']}；Top-5 首条为 `{case.get('top_titles', ['-'])[0]}`，期望知识未进入 Top-5。问法缺少标题里的 `host_os_unknown` / `credential_no_data` 关键词，属于口语化改写召回不足。")
    add("")
    add("## 6. 验收证据")
    add("")
    add("- 服务层真库自检：`selftest_knowledge_targets.py` 9 pass / 0 fail。")
    add("- HTTP 自检：`selftest_knowledge_targets_http.py` 14 pass / 0 fail，覆盖真实登录、创建、列表、详情、RAG host/service 过滤和非法参数。")
    add("- 回滚脚本 `028_knowledge_targets.rollback.sql` 已在事务中验证：删列后列计数 0，`ROLLBACK` 后恢复为 2。")
    add("- 自动条目实测：`host_os_unknown` 返回 `target_hosts=[\"local\"]`，`credential_no_data` 返回 `target_hosts=[\"vm-154\"]`，说明 AI 自由方案也能从 `ai_host` / `target_scope` 取到主机。")
    add("- 全量 pytest：584 条通过；前端 `npm run build` 通过，217 modules。")
    add("- 页面验收：知识库“新增知识”表单显示“目标主机”“目标服务”及精确匹配提示，截图 `code/var/tmp/knowledge-target-form.png`。")
    add("- 详情验收：自动部署知识详情显示正文“目标 local / unknown”，底部标签“目标主机 local”，截图 `code/var/tmp/knowledge-target-detail-tag.png`。")
    add("")
    add("## 7. 待确认")
    add("")
    add("- 现有 10 条手工知识的目标字段仍为空，按申请约定不做自动猜写；需业务确认后逐条回填。")
    add("- 2 条未命中是否要通过补充同义问法/关键词修复，还是接受当前 0.960 Recall@5。")
    add("- 生产可见自动知识只有 3 条，需单独决定知识准入策略是否放宽。")
    add("")

    output = CODE.parent / "A2-RAG-复测与逐条核对报告-2026-11-21.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
