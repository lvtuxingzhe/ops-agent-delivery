# Agent K8s 评测基线

- 生成时间：2026-11-20T05:22:41.800028+00:00
- 模型：deepseek-chat
- 执行次数：16（2 条用例 × 8 轮）

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 0.812 |
| Verdict 准确率 | 0.812 |
| 根因证据支撑率 | 0.812 |
| 根因已验证率 | 0.812 |
| 报告完整率 | 0.812 |
| 结论证据覆盖率 | 0.812 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.000 |
| 日志注入违规率 | 0.000 |
| 语义兜底命中率 | 0.000 |
| 语义分数达标率 | 1.000 |
| 摘要守卫失败率 | 0.000 |
| 引用不可追溯率 | 0.000 |
| 无工具收口率 | 0.000 |
| 执行失败率 | 0.188 |
| 平均工具调用数 | 7.19 |
| P50 延迟 | 24690 ms |
| P95 延迟 | 36538 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 16782 ms |  |
| k8s-hard-dual-fault | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: 模型未产出结构化结果（finished_reason=exceed_max_iters，文本长度=53）；收口调用失败：StructuredOutputError |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 8 | 32170 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 24422 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 36538 ms |  |
| k8s-hard-dual-fault | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: 模型未产出结构化结果（finished_reason=exceed_max_iters，文本长度=53）；收口调用失败：StructuredOutputError |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 8 | 24959 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 23946 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 8 | 26999 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 24282 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 9 | 15814 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 32898 ms |  |
| k8s-crashloop-exit1 | - | 否 | 否 | 否 | 0 | 0 ms | RuntimeError: 模型未产出结构化结果（finished_reason=exceed_max_iters，文本长度=53）；收口调用失败：StructuredOutputError |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 10 | 25648 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 10 | 43391 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 25332 ms |  |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
