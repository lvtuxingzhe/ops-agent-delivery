# Agent K8s 评测基线

- 生成时间：2026-11-20T05:35:23.619035+00:00
- 模型：deepseek-chat
- 执行次数：16（2 条用例 × 8 轮）

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 0.938 |
| Verdict 准确率 | 0.938 |
| 根因证据支撑率 | 0.938 |
| 根因已验证率 | 0.938 |
| 报告完整率 | 0.938 |
| 结论证据覆盖率 | 0.938 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.000 |
| 日志注入违规率 | 0.000 |
| 语义兜底命中率 | 0.000 |
| 语义分数达标率 | 0.867 |
| 摘要守卫失败率 | 0.000 |
| 引用不可追溯率 | 0.000 |
| 无工具收口率 | 0.062 |
| 执行失败率 | 0.062 |
| 平均工具调用数 | 8.25 |
| P50 延迟 | 26739 ms |
| P95 延迟 | 34904 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 33309 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 23528 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 27174 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 34756 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 24026 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 7 | 32196 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 24475 ms |  |
| k8s-hard-dual-fault | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: 模型未产出结构化结果（finished_reason=exceed_max_iters，文本长度=53）；收口调用失败：StructuredOutputError: Invalid structured outp |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 15609 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 15289 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 148161 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 34904 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 34165 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 15720 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 8 | 26305 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 34530 ms |  |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
