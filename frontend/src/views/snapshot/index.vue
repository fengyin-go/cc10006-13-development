<template>
  <section class="page" data-module="snapshot">
    <header class="page-head">
      <div>
        <h2>版本快照管理</h2>
        <p class="page-desc">
          快照按 草稿 → 待创建 → 已生效 → 已过期 单向流转；生效与恢复都要求依赖快照全部有效，
          恢复前还会检查目标表上的聚合视图与回填作业写入引用。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记快照记录</button>
        <button class="btn" type="button" @click="exportRows">导出版本快照清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>快照编号</span>
        <input v-model="filters.keyword" placeholder="按快照编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="notice" class="notice-text">{{ notice }}</p>
    <p v-if="missingDependencies.length" class="error-text">
      缺失依赖：{{ missingDependencies.join('、') }}
    </p>
    <p v-if="blockers.length" class="error-text">
      恢复被阻止：{{ blockers.join('、') }}
    </p>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="isActionDisabled(action, row)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无版本快照数据，可先登记快照记录</td>
        </tr>
      </tbody>
    </table>

    <section class="panel">
      <header class="panel-head">
        <h3>恢复回执</h3>
        <span class="muted-text">重复恢复请求返回同一张回执</span>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in receiptColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="receipt in receipts" :key="String(receipt['回执编号'])">
            <td v-for="column in receiptColumns" :key="column">
              {{ formatReceipt(receipt[column]) }}
            </td>
          </tr>
          <tr v-if="!receipts.length">
            <td :colspan="receiptColumns.length" class="empty-state">还没有恢复回执</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条版本快照记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Receipt = Record<string, string | number | string[] | null>

const ENDPOINT = '/api/snapshot'
const columns = ["快照编号", "目标表名", "快照时间", "依赖快照", "恢复校验", "状态", "恢复回执"]
const actions = ["提交创建", "校验恢复", "生效快照", "恢复快照", "标记过期"]
const statuses = ["草稿", "待创建", "已生效", "已过期"]
const receiptColumns = ["回执编号", "快照编号", "目标表名", "恢复时间", "目标表版本", "恢复校验", "依赖快照", "审计日志"]

const rows = ref<Row[]>([])
const receipts = ref<Receipt[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const missingDependencies = ref<string[]>([])
const blockers = ref<string[]>([])
const filters = ref<{ keyword: string; status: string }>({ keyword: '', status: '' })

const stats = computed(() => [
  { label: '快照记录总数', value: total.value },
  { label: '已生效快照', value: rows.value.filter((row) => row.status === '已生效').length },
  { label: '待创建快照', value: rows.value.filter((row) => row.status === '待创建').length },
  { label: '恢复回执', value: receipts.value.length },
])

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = ''
  notice.value = '快照记录登记入口尚未接入审批流'
}

function isActionDisabled(action: string, row: Row) {
  const status = String(row.status ?? '')
  if (action === '提交创建') return status !== '草稿'
  if (action === '标记过期') return status !== '已生效' && status !== '已过期'
  if (action === '生效快照') return status === '已过期'
  if (action === '恢复快照') return status !== '已生效'
  return status === '已过期'
}

function formatReceipt(value: string | number | string[] | null) {
  return Array.isArray(value) ? value.join('、') || '—' : value ?? '—'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  notice.value = ''
  missingDependencies.value = []
  blockers.value = []
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    missingDependencies.value = payload.missing_dependencies ?? []
    blockers.value = payload.blockers ?? []
    if (!payload.ok) {
      errorMessage.value = payload.message ?? '版本快照动作未生效'
      return
    }
    notice.value = payload.duplicate
      ? `${payload.message}（回执已命中，目标表未重复变更）`
      : payload.message
    await Promise.all([reload(), reloadReceipts()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本快照操作失败'
  }
}

async function reloadReceipts() {
  try {
    const response = await request(`${ENDPOINT}/receipts`)
    if (!response.ok) return
    const payload = await response.json()
    receipts.value = payload.items ?? []
  } catch {
    receipts.value = []
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({
    page: '1',
    size: '200',
  })
  if (filters.value.keyword) query.set('keyword', filters.value.keyword)
  if (filters.value.status) query.set('status', filters.value.status)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('快照记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本快照列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadReceipts()
})
</script>
