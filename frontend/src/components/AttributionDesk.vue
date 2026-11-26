<template>
  <section class="desk" data-testid="attribution-desk" :data-gateway="gatewayId || undefined">
    <header class="desk-head">
      <div>
        <h3>设备移交与迟到读数 · 归属处置回执</h3>
        <p class="desk-tip">
          以<strong>实际采样时刻</strong>和<strong>当时有效的挂载区间</strong>核准报文来源；
          交接办理中不凭当前网关信息换归属。无法证明来源的读数留在待核项，不能填入测点序列；
          同一采样位置的重发副本只入账一次。
        </p>
      </div>
      <div class="desk-head-actions">
        <button class="btn ghost" type="button" data-testid="reset-scenario" @click="resetScenario">重置D场景</button>
      </div>
    </header>

    <p v-if="actionMessage" class="desk-message" data-testid="desk-message">{{ actionMessage }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="desk-error">{{ errorMessage }}</p>

    <div class="desk-grid">
      <!-- ============ 归属处置回执 ============ -->
      <article class="desk-card receipt-card" data-testid="attribution-receipt">
        <div class="card-head">
          <h4>可核签迟到读数的归属处置回执</h4>
          <div class="receipt-open">
            <input v-model="receiptRef" data-testid="receipt-ref-input" placeholder="回执编号，如 RCP-D-01" />
            <button class="btn" type="button" data-testid="reopen-receipt" @click="openReceipt()">打开 / 重新打开回执</button>
          </div>
        </div>

        <template v-if="receipt">
          <dl class="receipt-meta">
            <div><dt>回执编号</dt><dd data-testid="receipt-no">{{ receipt['回执编号'] }}</dd></div>
            <div><dt>关联移交</dt><dd>{{ receipt['移交编号'] || '—' }}</dd></div>
            <div><dt>设备编号</dt><dd>{{ receipt['设备编号'] }}</dd></div>
            <div><dt>移交时间</dt><dd data-testid="receipt-handover-time">{{ receipt['移交时间'] || '—' }}</dd></div>
            <div class="span2"><dt>关联报文来源</dt><dd data-testid="receipt-sources">{{ receipt['报文来源'] || '—' }}</dd></div>
          </dl>

          <p class="pending-scope" data-testid="pending-scope" :data-count="pendingItems.length">
            尚待核签的范围（待核项）：<strong>{{ pendingItems.length }}</strong> 项
            <span v-if="pendingRefs.length" class="pending-refs">{{ pendingRefs.join('、') }}</span>
          </p>

          <div class="table-scroll">
            <table class="data-table receipt-table" data-testid="receipt-items">
              <thead>
                <tr>
                  <th>条目</th><th>报文来源（哪台网关）</th><th>上送编号（哪次上送）</th>
                  <th>数值</th><th>实际采样时刻</th><th>核验结论 / 归属依据</th>
                  <th>核签状态</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in receipt.items || []"
                  :key="String(item.id)"
                  :data-testid="`receipt-item-${item.id}`"
                  :data-status="String(item['核签状态'])"
                  :data-pending="String(item.pending)"
                >
                  <td>{{ item['条目编号'] }}</td>
                  <td data-testid="item-source">{{ item['报文来源'] || item['网关编号'] }}</td>
                  <td>{{ item['上送编号'] || item['报文编号'] }}</td>
                  <td>{{ item['数值'] }}</td>
                  <td>{{ item['实际采样时刻'] }}</td>
                  <td class="verdict">{{ item['核验结论'] }}</td>
                  <td>
                    <span class="status-pill" :data-kind="statusKind(item)">{{ item['核签状态'] }}</span>
                    <div v-if="item['核签人']" class="signer">核签：{{ item['核签人'] }}</div>
                  </td>
                  <td class="row-actions">
                    <button
                      v-if="item['核签状态'] === '待核签'"
                      class="link"
                      type="button"
                      :data-testid="`sign-item-${item.id}`"
                      @click="signItem(item)"
                    >核签</button>
                    <span v-else-if="item['核签状态'] === '重复副本'" class="dup-tag">重发副本</span>
                    <span v-else class="muted">—</span>
                  </td>
                </tr>
                <tr v-if="!(receipt.items || []).length">
                  <td colspan="8" class="empty-state">回执下暂无报文条目</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="card-actions">
            <button class="btn primary" type="button" data-testid="sign-all" @click="signAll">核签全部可证明条目</button>
            <span class="muted">来源无法证明的条目会保留在待核项，不会进入序列。</span>
          </div>
        </template>
        <p v-else class="empty-state">输入回执编号后打开，或先登记移交 / 上送迟到读数，系统会自动生成回执。</p>
      </article>

      <!-- ============ 设备移交登记 ============ -->
      <article class="desk-card" data-testid="handover-panel">
        <div class="card-head"><h4>设备移交登记（东网关 → 西网关）</h4></div>
        <form class="desk-form" data-testid="handover-form" @submit.prevent="submitHandover">
          <label><span>设备编号</span>
            <input v-model="handoverForm.device_id" data-testid="handover-device" required />
          </label>
          <label><span>源网关（移交前挂载）</span>
            <input v-model="handoverForm.source_gateway" data-testid="handover-source" required />
          </label>
          <label><span>目标网关（待接管）</span>
            <input v-model="handoverForm.target_gateway" data-testid="handover-target" required />
          </label>
          <label><span>移交时间 / 待接管时刻</span>
            <input v-model="handoverForm.handover_time" type="datetime-local" step="1" data-testid="handover-time" required />
          </label>
          <button class="btn primary" type="submit" data-testid="submit-handover">登记移交</button>
        </form>

        <table class="data-table compact" data-testid="handover-list">
          <thead><tr><th>移交编号</th><th>设备</th><th>方向</th><th>移交时间</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="ho in handovers" :key="String(ho.id)" :data-testid="`handover-row-${ho.id}`">
              <td>{{ ho['移交编号'] }}</td>
              <td>{{ ho['设备编号'] }}</td>
              <td>{{ ho['源网关'] || ho['源网关编号'] }} → {{ ho['目标网关'] || ho['目标网关编号'] }}</td>
              <td>{{ ho['移交时间'] }}</td>
              <td><span class="status-pill" :data-kind="ho['状态'] === '已确认' ? 'ok' : 'wait'">{{ ho['状态'] }}</span></td>
              <td>
                <button
                  v-if="ho['状态'] !== '已确认'"
                  class="link" type="button"
                  :data-testid="`confirm-handover-${ho.id}`"
                  @click="confirmHandover(ho)"
                >移交确认</button>
                <span v-else class="muted">已确认</span>
              </td>
            </tr>
            <tr v-if="!handovers.length"><td colspan="6" class="empty-state">暂无移交登记</td></tr>
          </tbody>
        </table>
      </article>

      <!-- ============ 迟到读数上送 ============ -->
      <article class="desk-card" data-testid="reading-panel">
        <div class="card-head"><h4>迟到读数上送（关联报文来源）</h4></div>
        <div class="quick-row">
          <button class="btn ghost" type="button" data-testid="fill-east-124" @click="fillReading('east')">填入东机 12.4（交接前）</button>
          <button class="btn ghost" type="button" data-testid="fill-west-128" @click="fillReading('west')">填入西机 12.8（待接管时刻）</button>
          <button class="btn ghost" type="button" data-testid="fill-resend-124" @click="fillReading('resend')">填入东机重发副本</button>
        </div>
        <form class="desk-form" data-testid="reading-form" @submit.prevent="submitReading">
          <label><span>设备编号</span>
            <input v-model="readingForm.device_id" data-testid="reading-device" required />
          </label>
          <label><span>测点编号</span>
            <input v-model="readingForm.point_id" data-testid="reading-point" required />
          </label>
          <label><span>报文来源（上送网关）</span>
            <input v-model="readingForm.gateway_id" data-testid="reading-gateway" required />
          </label>
          <label><span>数值</span>
            <input v-model="readingForm.value" inputmode="decimal" data-testid="reading-value" required />
          </label>
          <label><span>实际采样时刻</span>
            <input v-model="readingForm.sampled_at" type="datetime-local" step="1" data-testid="reading-time" required />
          </label>
          <label><span>上送编号 / 报文编号（可空，自动生成）</span>
            <input v-model="readingForm.message_id" data-testid="reading-message" placeholder="如 MSG-D-E-124" />
          </label>
          <label class="check-field"><input v-model="readingForm.resend" type="checkbox" data-testid="reading-resend" /> 重发副本</label>
          <button class="btn primary" type="submit" data-testid="submit-reading">上送迟到读数</button>
        </form>
      </article>

      <!-- ============ 挂载区间 ============ -->
      <article class="desk-card" data-testid="mount-panel">
        <div class="card-head"><h4>当时有效的挂载区间</h4></div>
        <table class="data-table compact" data-testid="mount-list">
          <thead><tr><th>挂载编号</th><th>设备</th><th>网关</th><th>生效起</th><th>生效止</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="m in mounts" :key="String(m.id)" :data-testid="`mount-row-${m.id}`">
              <td>{{ m['挂载编号'] }}</td>
              <td>{{ m['设备编号'] }}</td>
              <td>{{ m['网关名称'] || m['网关编号'] }}</td>
              <td>{{ m['生效起'] }}</td>
              <td>{{ m['生效止'] || '至今' }}</td>
              <td><span class="status-pill" :data-kind="mountKind(m['状态'])">{{ m['状态'] }}</span></td>
            </tr>
            <tr v-if="!mounts.length"><td colspan="6" class="empty-state">暂无挂载区间</td></tr>
          </tbody>
        </table>
      </article>

      <!-- ============ 测点确认序列 ============ -->
      <article class="desk-card span2" data-testid="series-panel">
        <div class="card-head">
          <h4>测点确认序列（仅核签通过、按采样位置去重）</h4>
          <strong data-testid="series-count" :data-count="series.length">共 {{ series.length }} 条确认记录</strong>
        </div>
        <div class="table-scroll">
          <table class="data-table" data-testid="series-list">
            <thead><tr><th>记录编号</th><th>测点</th><th>实际采样时刻</th><th>数值</th><th>归属网关</th><th>来源报文</th><th>核签人 / 时间</th></tr></thead>
            <tbody>
              <tr v-for="rec in series" :key="String(rec.id)" :data-testid="`series-row-${rec.id}`" :data-gateway="rec.gateway_id">
                <td>{{ rec['记录编号'] }}</td>
                <td>{{ rec['测点编号'] }}</td>
                <td>{{ rec['实际采样时刻'] }}</td>
                <td data-testid="series-value">{{ rec['数值'] }}</td>
                <td data-testid="series-gateway">{{ rec['归属网关'] || rec['归属网关编号'] }}</td>
                <td>{{ rec['来源报文编号'] }}</td>
                <td>{{ rec['核签人'] }} · {{ rec['确认时间'] }}</td>
              </tr>
              <tr v-if="!series.length"><td colspan="7" class="empty-state">尚无归属正确的确认记录，先在上方核签可证明的读数</td></tr>
            </tbody>
          </table>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { handoverApi } from '@/api/handover'

const props = withDefaults(defineProps<{
  deviceId?: string
  pointId?: string
  receiptRefId?: string
  gatewayId?: string
}>(), {
  deviceId: 'D',
  pointId: 'TS-D-01',
  receiptRefId: 'RCP-D-01',
  gatewayId: '',
})

const receiptRef = ref(props.receiptRefId)
const receipt = ref<any>(null)
const handovers = ref<any[]>([])
const mounts = ref<any[]>([])
const series = ref<any[]>([])
const actionMessage = ref('')
const errorMessage = ref('')

const handoverForm = ref({
  device_id: props.deviceId,
  source_gateway: '东网关',
  target_gateway: '西网关',
  handover_time: '2026-11-26T10:00:00',
})
const readingForm = ref({
  device_id: props.deviceId,
  point_id: props.pointId,
  gateway_id: '东网关',
  value: '12.4',
  sampled_at: '2026-11-26T09:59:00',
  message_id: '',
  resend: false,
})

const pendingItems = computed<any[]>(() => receipt.value?.pending_items || receipt.value?.['待核签项'] || [])
const pendingRefs = computed<string[]>(() => receipt.value?.pending_refs || [])

function flash(message: string) {
  actionMessage.value = message
  window.setTimeout(() => { actionMessage.value = '' }, 6000)
}

async function refreshAll() {
  errorMessage.value = ''
  try {
    const [ho, mo, sr] = await Promise.all([
      handoverApi.listHandovers(props.deviceId),
      handoverApi.listMounts(props.deviceId),
      handoverApi.series(props.pointId, props.deviceId),
    ])
    handovers.value = ho.items || []
    mounts.value = mo.items || []
    series.value = sr.items || []
    if (receiptRef.value) {
      try {
        receipt.value = await handoverApi.getReceipt(receiptRef.value)
      } catch {
        receipt.value = null
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据加载失败'
  }
}

async function openReceipt(ref?: string) {
  const target = ref || receiptRef.value
  if (!target) return
  receiptRef.value = target
  try {
    receipt.value = await handoverApi.reopenReceipt(target)
    flash(`已重新打开回执 ${target}，尚待核签 ${receipt.value.pending_count} 项`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执打开失败'
  }
}

async function signAll() {
  if (!receiptRef.value) return
  try {
    const result = await handoverApi.countersign(receiptRef.value)
    flash(result.message || '核签完成')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核签失败'
  }
}

async function signItem(item: any) {
  try {
    const result = await handoverApi.countersignItem(item['条目编号'])
    flash(result.message || '核签完成')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核签失败'
  }
}

async function submitHandover() {
  try {
    const result = await handoverApi.registerHandover({ ...handoverForm.value })
    if (!result.ok) throw new Error(result.message)
    if (result.receipt?.['回执编号']) receiptRef.value = result.receipt['回执编号']
    flash(result.message || '移交已登记')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '移交登记失败'
  }
}

async function confirmHandover(ho: any) {
  try {
    const result = await handoverApi.confirmHandover(ho['移交编号'])
    if (!result.ok) throw new Error(result.message)
    if (result.receipt) receipt.value = result.receipt
    flash(result.message || '移交已确认，回执范围已重新核验')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '移交确认失败'
  }
}

function fillReading(kind: 'east' | 'west' | 'resend') {
  if (kind === 'east') {
    readingForm.value = {
      device_id: props.deviceId, point_id: props.pointId, gateway_id: '东网关',
      value: '12.4', sampled_at: '2026-11-26T09:59:00', message_id: '', resend: false,
    }
  } else if (kind === 'west') {
    readingForm.value = {
      device_id: props.deviceId, point_id: props.pointId, gateway_id: '西网关',
      value: '12.8', sampled_at: '2026-11-26T10:00:00', message_id: '', resend: false,
    }
  } else {
    readingForm.value = {
      device_id: props.deviceId, point_id: props.pointId, gateway_id: '东网关',
      value: '12.4', sampled_at: '2026-11-26T09:59:00',
      message_id: 'MSG-D-E-124-RESEND', resend: true,
    }
  }
}

async function submitReading() {
  try {
    const payload: Record<string, unknown> = {
      ...readingForm.value,
      value: Number(readingForm.value.value),
    }
    if (!payload.message_id) delete payload.message_id
    const result = await handoverApi.submitReading(payload)
    if (!result.ok) throw new Error(result.message)
    if (result.receipt?.['回执编号']) receiptRef.value = result.receipt['回执编号']
    flash(result.duplicate ? `重发副本：${result.message}` : result.message || '读数已上送')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读数上送失败'
  }
}

async function resetScenario() {
  try {
    const result = await handoverApi.resetScenario()
    if (!result.ok) throw new Error(result.message)
    receiptRef.value = props.receiptRefId
    flash('D / 东网关 / 西网关场景已重置到起始状态')
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '场景重置失败'
  }
}

function statusKind(item: any): string {
  const status = String(item['核签状态'] || item.status || '')
  if (status === '已核签') return 'ok'
  if (status === '重复副本') return 'dup'
  if (status.includes('存疑') || item.abnormal) return 'block'
  return 'wait'
}

function mountKind(status: string): string {
  if (status === '有效') return 'ok'
  if (status === '待接管') return 'wait'
  return 'off'
}

onMounted(refreshAll)
</script>

<style scoped>
.desk { display: flex; flex-direction: column; gap: 12px; margin: 12px 0 24px; }
.desk-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.desk-head h3 { margin: 0 0 4px; font-size: 17px; }
.desk-tip { color: var(--muted); font-size: 13px; margin: 0; max-width: 900px; line-height: 1.7; }
.desk-message { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; padding: 8px 12px; border-radius: 8px; font-size: 13px; margin: 0; }
.desk-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(320px, 1fr); gap: 12px; align-items: start; }
.desk-card { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.desk-card.span2 { grid-column: 1 / -1; }
.receipt-card { grid-column: 1 / -1; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.card-head h4 { margin: 0; font-size: 15px; }
.receipt-open { display: flex; gap: 8px; }
.receipt-open input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; min-width: 200px; }
.receipt-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 16px; margin: 0 0 10px; }
.receipt-meta .span2 { grid-column: span 2; }
.receipt-meta dt { font-size: 12px; color: var(--muted); }
.receipt-meta dd { margin: 2px 0 0; font-size: 13px; }
.pending-scope { background: #fffaeb; border: 1px solid #fedf89; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin: 0 0 10px; }
.pending-refs { color: #b54708; margin-left: 8px; }
.table-scroll { overflow-x: auto; }
.verdict { min-width: 220px;; color: #475467; }
.signer { font-size: 11px; color: var(--muted); margin-top: 2px; }
.card-actions { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.desk-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
.desk-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.desk-form input { padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; color: #1f2937; }
.desk-form .check-field { flex-direction: row; align-items: center; gap: 6px; justify-content: flex-start; color: #344054; }
.desk-form button { align-self: end; }
.quick-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.data-table.compact th, .data-table.compact td { padding: 6px 8px; font-size: 12px; }
.status-pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: #f2f4f7; color: #344054; }
.status-pill[data-kind='ok'] { background: #ecfdf3; color: #067647; }
.status-pill[data-kind='wait'] { background: #fffaeb; color: #b54708; }
.status-pill[data-kind='block'] { background: #fef3f2; color: #b42318; }
.status-pill[data-kind='dup'] { background: #eef4ff; color: #3538cd; }
.dup-tag { color: #3538cd; font-size: 12px; }
.muted { color: var(--muted); font-size: 12px; }
@media (max-width: 1100px) {
  .desk-grid { grid-template-columns: 1fr; }
  .receipt-meta { grid-template-columns: repeat(2, 1fr); }
}
</style>
