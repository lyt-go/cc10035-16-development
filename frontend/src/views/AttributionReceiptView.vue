<template>
  <section class="receipt-page">
    <header class="page-head">
      <div>
        <h2>归属处置回执</h2>
        <p class="page-desc">
          网关设备移交登记与迟到读数的联合操作：从详情页关联报文来源和移交时间，
          服务端按实际采样时刻与当时有效的挂载区间决定测点能否收录。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/gateway_mgmt">返回网关管理详情</RouterLink>
      </div>
    </header>

    <!-- 核签工作台：完整叙述流程 -->
    <AttributionDesk />

    <!-- 通用资源清单：保留 scaffold 的登记 / 查询 / 行动作与 testid 约定 -->
    <div class="generic-wrap" data-testid="receipt-generic-table">
      <WorkbenchView module-key="attribution_receipt" />
    </div>
  </section>
</template>

<script setup lang="ts">
import AttributionDesk from '@/components/AttributionDesk.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
</script>

<style scoped>
.generic-wrap {
  margin-top: 24px;
  border-top: 2px dashed var(--border, #d0d5dd);
  padding-top: 12px;
}
</style>
