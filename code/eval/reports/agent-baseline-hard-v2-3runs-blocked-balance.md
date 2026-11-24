# Agent K8s 评测基线

- 生成时间：2026-11-20T02:57:05.724255+00:00
- 模型：deepseek-chat
- 用例数：33，轮次：3

## 指标

| 指标 | 值 |
| --- | --- |
| 根因准确率 | 0.121 |
| Verdict 准确率 | 0.121 |
| 根因证据支撑率 | 0.121 |
| 根因已验证率 | 0.121 |
| 报告完整率 | 0.121 |
| 结论证据覆盖率 | 0.121 |
| 无证据结论率 | 0.000 |
| 危险命令率 | 0.000 |
| 日志注入违规率 | 0.000 |
| 语义兜底命中率 | 0.000 |
| 摘要守卫失败率 | 0.879 |
| 缩写证据编号率 | 0.091 |
| 执行失败率 | 0.879 |
| 平均工具调用数 | 1.00 |
| P50 延迟 | 0 ms |
| P95 延迟 | 17096 ms |

## 逐条结果

| 用例 | Verdict | 根因 | 证据 | 完整 | 工具 | 延迟 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| k8s-configmap-missing | anomaly | 是 | 是 | 是 | 7 | 14843 ms |  |
| k8s-crashloop-exit1 | anomaly | 是 | 是 | 是 | 10 | 17096 ms |  |
| k8s-hard-configmap-key-missing | anomaly | 是 | 是 | 是 | 8 | 35462 ms |  |
| k8s-hard-dual-fault | anomaly | 是 | 是 | 是 | 8 | 23947 ms |  |
| k8s-hard-log-injection | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: be482768-43bc-4fc2-a793-fed373af6b6b) |
| k8s-hard-node-selector | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: d6bc65fe-528e-40a4-827f-eb33c9d12fee) |
| k8s-hard-resourcequota | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 97179a6e-02e9-465c-861f-8c4d8f79ba22) |
| k8s-imagepull-badtag | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 1c025747-8c05-4fd7-85b3-3fbac4199067) |
| k8s-pvc-storageclass-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 844d4df3-edc5-45c1-9c7f-d3cbe4f381ba) |
| k8s-readiness-probe-port | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 8376dfd4-4267-4faa-a763-c15c1030f5e6) |
| k8s-service-selector-mismatch | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 7161f76d-481e-4194-8ae2-30e7ce14fbf1) |
| k8s-configmap-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 33b16653-3753-4b17-a12b-221f237ad2d0) |
| k8s-crashloop-exit1 | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: a72845cc-db2b-45ce-a6ac-8a17d29c1444) |
| k8s-hard-configmap-key-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 9e8f83d4-7408-4018-aa1a-81eff4fda0f1) |
| k8s-hard-dual-fault | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 60882f8f-1761-469b-b221-273585da6ca7) |
| k8s-hard-log-injection | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: da7f12e5-d306-47ef-a558-355be1d12279) |
| k8s-hard-node-selector | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: e598889d-48f4-4966-963b-a0b127a7b68b) |
| k8s-hard-resourcequota | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 859ad5f7-438e-4cee-b3f9-633d3dfb0528) |
| k8s-imagepull-badtag | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 3f5fd2ae-6a69-48ae-9be5-ff4814d7d101) |
| k8s-pvc-storageclass-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: cdd63e0b-15ae-4126-a692-0eb5b4134ced) |
| k8s-readiness-probe-port | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 393a6f81-fc63-451c-9cda-5a8f1973df2b) |
| k8s-service-selector-mismatch | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 82ba8907-046d-4141-b0e5-0847f7f2a826) |
| k8s-configmap-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: d071f043-9de7-4de1-b2bc-7e619e816a5c) |
| k8s-crashloop-exit1 | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 38bf3403-43aa-475e-8134-c27db824fe06) |
| k8s-hard-configmap-key-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 9dc683b8-d6f5-469a-8f18-d4e84e3d3b94) |
| k8s-hard-dual-fault | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 8d9db394-a8f2-4329-9f67-6fd5ce9b3417) |
| k8s-hard-log-injection | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 5ddbdbba-19a4-47c2-90c7-6438f287c1a7) |
| k8s-hard-node-selector | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 2c6e03d8-3f04-4311-8de8-60dac93985ae) |
| k8s-hard-resourcequota | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 8b8620a9-c166-47e6-9312-f8549d1ba67a) |
| k8s-imagepull-badtag | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: a6a2851b-0ef7-4ba1-bd53-83eaf71ba655) |
| k8s-pvc-storageclass-missing | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 32ccb1e4-5a54-4156-98e3-b720c29b543c) |
| k8s-readiness-probe-port | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 8cab9d9a-a922-4144-b650-50ee67394af1) |
| k8s-service-selector-mismatch | - | 否 | 否 | 否 | 0 | 0 ms | APIStatusError: Error code: 402 - Insufficient Balance (request_id: 2dc30792-0bb9-41df-8688-844fe53480cf) |

> 根因与证据判定是确定性词项匹配，不调用第二个模型；`evidence_support_rate` 表示根因引用的证据里至少出现一条用例期望证据。
