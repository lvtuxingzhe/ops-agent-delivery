# RAG 检索评测报告

- 生成时间：2026-11-21T11:58:15.466576+00:00
- 用例数：50，Top-K：5

## 指标

| 指标 | 值 |
| --- | --- |
| Recall@5 | 0.960 |
| Top-1 准确率 | 0.880 |
| MRR | 0.912 |
| 空召回率 | 0.000 |
| 危险误召回率 | 0.000 |
| 有危险标签的用例 | 50 |

## 分组指标

| 分组 | 用例数 | Recall@5 | Top-1 | 空召回率 |
| --- | --- | --- | --- | --- |
| manual | 40 | 1.000 | 0.950 | 0.000 |
| deployment | 10 | 0.800 | 0.600 | 0.000 |

## 逐条结果

| 用例 | 命中 | 排名 | 模式 | 命中条目 | Top-1 |
| --- | --- | --- | --- | --- | --- |
| prod-man-01a | 是 | 1 | hybrid | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| prod-man-01b | 是 | 1 | hybrid | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| prod-man-01c | 是 | 1 | hybrid | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| prod-man-01d | 是 | 1 | hybrid | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 | manual-114e4d1a-52e3-4f96-b31e-55082e266be4 |
| prod-man-02a | 是 | 1 | hybrid | manual-ad163663-38bf-4be6-81a5-2b1fec51aced | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| prod-man-02b | 是 | 1 | hybrid | manual-ad163663-38bf-4be6-81a5-2b1fec51aced | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| prod-man-02c | 是 | 1 | hybrid | manual-ad163663-38bf-4be6-81a5-2b1fec51aced | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| prod-man-02d | 是 | 1 | hybrid | manual-ad163663-38bf-4be6-81a5-2b1fec51aced | manual-ad163663-38bf-4be6-81a5-2b1fec51aced |
| prod-man-03a | 是 | 2 | hybrid | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-03b | 是 | 1 | hybrid | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| prod-man-03c | 是 | 1 | hybrid | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| prod-man-03d | 是 | 1 | hybrid | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| prod-man-04a | 是 | 1 | hybrid | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-man-04b | 是 | 1 | hybrid | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-man-04c | 是 | 1 | hybrid | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-man-04d | 是 | 1 | hybrid | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-man-05a | 是 | 1 | hybrid | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| prod-man-05b | 是 | 1 | hybrid | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| prod-man-05c | 是 | 1 | hybrid | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| prod-man-05d | 是 | 1 | hybrid | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 | manual-b7f6eae6-68db-49d9-8958-3fdf39d86fa8 |
| prod-man-06a | 是 | 1 | hybrid | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a |
| prod-man-06b | 是 | 1 | hybrid | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a |
| prod-man-06c | 是 | 2 | hybrid | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-06d | 是 | 1 | hybrid | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a | manual-e0fc5043-c0b7-4875-9173-4659cd50f86a |
| prod-man-07a | 是 | 1 | hybrid | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| prod-man-07b | 是 | 1 | hybrid | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| prod-man-07c | 是 | 1 | hybrid | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| prod-man-07d | 是 | 1 | hybrid | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e | manual-ecf9ae9c-1055-441d-b101-68103b9ee91e |
| prod-man-08a | 是 | 1 | hybrid | manual-5c08e202-637b-42cf-99b0-1a10af317767 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| prod-man-08b | 是 | 1 | hybrid | manual-5c08e202-637b-42cf-99b0-1a10af317767 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| prod-man-08c | 是 | 1 | hybrid | manual-5c08e202-637b-42cf-99b0-1a10af317767 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| prod-man-08d | 是 | 1 | hybrid | manual-5c08e202-637b-42cf-99b0-1a10af317767 | manual-5c08e202-637b-42cf-99b0-1a10af317767 |
| prod-man-09a | 是 | 1 | hybrid | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-09b | 是 | 1 | hybrid | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-09c | 是 | 1 | hybrid | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-09d | 是 | 1 | hybrid | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |
| prod-man-10a | 是 | 1 | hybrid | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| prod-man-10b | 是 | 1 | hybrid | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| prod-man-10c | 是 | 1 | hybrid | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| prod-man-10d | 是 | 1 | hybrid | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 | manual-de9a0b96-1a39-4555-8f92-ad2b10facbc2 |
| prod-dep-15a | 是 | 1 | hybrid | dep-c26e9d64-df84-4026-b00d-120126e4a370 | dep-c26e9d64-df84-4026-b00d-120126e4a370 |
| prod-dep-15b | 是 | 1 | hybrid | dep-c26e9d64-df84-4026-b00d-120126e4a370 | dep-c26e9d64-df84-4026-b00d-120126e4a370 |
| prod-dep-15c | 是 | 3 | hybrid | dep-c26e9d64-df84-4026-b00d-120126e4a370 | manual-39268cbd-c5a4-4b17-aa47-1051677daea3 |
| prod-dep-15d | 是 | 1 | hybrid | dep-c26e9d64-df84-4026-b00d-120126e4a370 | dep-c26e9d64-df84-4026-b00d-120126e4a370 |
| prod-dep-15e | 否 | - | hybrid | - | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-dep-17a | 是 | 1 | hybrid | dep-e55bba09-ef06-4994-8271-c02755b09015 | dep-e55bba09-ef06-4994-8271-c02755b09015 |
| prod-dep-17b | 是 | 4 | hybrid | dep-e55bba09-ef06-4994-8271-c02755b09015 | manual-e2e1ec91-0d13-49f1-bc29-eaeab721c72e |
| prod-dep-17c | 是 | 1 | hybrid | dep-e55bba09-ef06-4994-8271-c02755b09015 | dep-e55bba09-ef06-4994-8271-c02755b09015 |
| prod-dep-17d | 是 | 1 | hybrid | dep-e55bba09-ef06-4994-8271-c02755b09015 | dep-e55bba09-ef06-4994-8271-c02755b09015 |
| prod-dep-17e | 否 | - | hybrid | - | manual-7f2c619f-9480-4bbe-a868-02a48e255e93 |

> `dangerous_false_recall_rate` 只在有 `dangerous_terms` 标签的用例上统计；无标签时显示 N/A，不能把 N/A 当作 0。
