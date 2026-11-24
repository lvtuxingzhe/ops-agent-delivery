<script setup lang="ts">
/**
 * 新建排查页：只保留一个自然语言入口。
 * 真正的输入组件与首页共用，避免两套交互。
 */

import QuickInvestigation from '../components/QuickInvestigation.vue'
import DataSourceStatus from '../components/DataSourceStatus.vue'
</script>

<template>
  <div class="page-stack">
    <QuickInvestigation />
    <DataSourceStatus />

    <section class="card">
      <h2>提交之后会发生什么</h2>
      <ol class="flow">
        <li><span>1</span><strong>Agent 自动排查</strong><small>按描述查指标、日志和状态，边查边把证据挂到这次排查上。</small></li>
        <li><span>2</span><strong>生成报告</strong><small>给出结论、证据和“还没确认”的部分；缺目标时会在报告里标出来。</small></li>
        <li><span>3</span><strong>需要执行时停下来</strong><small>只有要改机器、跑命令时，才进入待审批等你确认。</small></li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.flow {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.flow li {
  display: grid;
  grid-template-columns: 20px 120px minmax(0, 1fr);
  align-items: baseline;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-soft);
}

.flow li span {
  color: var(--accent-ink);
  font-family: var(--mono);
  font-size: calc(10px * var(--font-scale, 1));
  font-weight: 700;
}

.flow li strong {
  font-size: calc(12px * var(--font-scale, 1));
  font-weight: 600;
}

.flow li small {
  color: var(--muted);
  font-size: calc(11px * var(--font-scale, 1));
  line-height: 1.6;
}

@media (max-width: 640px) {
  .flow li {
    grid-template-columns: 20px minmax(0, 1fr);
  }

  .flow li small {
    grid-column: 2;
  }
}
</style>
