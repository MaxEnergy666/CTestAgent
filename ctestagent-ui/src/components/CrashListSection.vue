<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { testStore } from '../stores/testStore'
import { fetchLatestReport } from '../api/runtimeControl'

const severityFilter = ref('all')
const typeFilter = ref('all')
const recentCrashIds = ref([])
const crashHighlightTimerMap = new Map()

const latestReport = computed(() => testStore.latestReport?.report || {})
const latestReportBugs = computed(() => (Array.isArray(latestReport.value?.bugs) ? latestReport.value.bugs : []))
const hasLatestReport = computed(() => Boolean(testStore.latestReport?.report))

const effectiveCrashes = computed(() => {
  const live = Array.isArray(testStore.crashes) ? testStore.crashes : []
  if (live.length > 0) return live
  return latestReportBugs.value.map((bug, index) => {
    const evidence = String(bug.evidence || '').toLowerCase()
    const verification = evidence === 'static' || String(bug.input || '').toLowerCase().includes('static analysis')
      ? 'static'
      : (bug.verified === false ? 'false_positive' : 'confirmed')
    return {
      id: bug.id || `bug_${index}`,
      type: bug.type || 'unknown',
      input: bug.input || '',
      stackTrace: bug.stack_trace || bug.stackTrace || bug.description || `${bug.type || 'unknown'} (rc=${Number(bug.return_code || 0)})`,
      description: bug.description || '',
      location: bug.location || '',
      risk: bug.risk || bug.risk_summary || bug.description || '',
      occurrences: Math.max(1, Number(bug.occurrences || 1)),
      verified: verification === 'confirmed',
      verification,
      evidence,
      severity: String(bug.risk_level || bug.severity || 'medium').toLowerCase(),
    }
  })
})

const crashTypeOptions = computed(() => ['all', ...Array.from(new Set(effectiveCrashes.value.map((item) => item.type).filter(Boolean)))])
const filteredCrashes = computed(() => effectiveCrashes.value.filter((item) => {
  const severityOk = severityFilter.value === 'all' || normalizeSeverity(item.severity) === severityFilter.value
  const typeOk = typeFilter.value === 'all' || item.type === typeFilter.value
  return severityOk && typeOk
}))

let lastDefectAutoFetchAt = 0
watch(() => [filteredCrashes.value.length, hasLatestReport.value, testStore.status], async ([count, hasReport, status]) => {
  if (Number(count || 0) > 0 || hasReport) return
  if (status !== 'finished' && status !== 'running') return
  const now = Date.now()
  if (now - lastDefectAutoFetchAt < 1500) return
  lastDefectAutoFetchAt = now
  await fetchLatestReport(false)
})

watch(() => testStore.crashes.length, (next, prev) => {
  if (next <= prev) return
  const latest = testStore.crashes?.[0]
  if (!latest?.id) return
  recentCrashIds.value = [latest.id, ...recentCrashIds.value.filter((id) => id !== latest.id)].slice(0, 8)
  window.clearTimeout(crashHighlightTimerMap.get(latest.id))
  const timer = window.setTimeout(() => {
    recentCrashIds.value = recentCrashIds.value.filter((id) => id !== latest.id)
    crashHighlightTimerMap.delete(latest.id)
  }, 2200)
  crashHighlightTimerMap.set(latest.id, timer)
})

function normalizeSeverity(value = '') {
  const lower = String(value).toLowerCase()
  if (lower === 'critical' || lower === 'high') return 'high'
  if (lower === 'medium') return 'medium'
  return 'low'
}

function severityTagType(value = '') {
  const severity = normalizeSeverity(value)
  if (severity === 'high') return 'danger'
  if (severity === 'medium') return 'warning'
  return ''
}

function severityLabel(value = '') {
  const severity = normalizeSeverity(value)
  if (severity === 'high') return '高'
  if (severity === 'medium') return '中'
  return '低'
}

function resolveVerificationState(item = {}) {
  const raw = String(item?.verification || '').toLowerCase()
  if (['static', 'confirmed', 'false_positive'].includes(raw)) return raw
  if (String(item?.evidence || '').toLowerCase() === 'static') return 'static'
  return item?.verified === false ? 'false_positive' : 'confirmed'
}

function verificationLabel(item = {}) {
  const state = resolveVerificationState(item)
  if (state === 'static') return '静态发现'
  if (state === 'false_positive') return '误报'
  return '已确认'
}

function verificationClass(item = {}) {
  const state = resolveVerificationState(item)
  if (state === 'static') return 'verify-static'
  if (state === 'false_positive') return 'verify-bad'
  return 'verify-ok'
}

function sanitizeText(value = '') {
  return String(value ?? '').replace(/[^\x09\x0A\x0D\x20-\x7E]/g, '·')
}

function clipText(value = '', maxLen = 520) {
  const text = sanitizeText(value)
  return text.length <= maxLen ? text : `${text.slice(0, maxLen)}...`
}

onBeforeUnmount(() => {
  crashHighlightTimerMap.forEach((timer) => window.clearTimeout(timer))
  crashHighlightTimerMap.clear()
})
</script>

<template>
  <section class="dashboard-section crash-section">
    <div class="section-heading">
      <div>
        <p class="section-kicker">Defects</p>
        <h2>缺陷列表</h2>
      </div>
      <div class="defect-toolbar">
        <el-select v-model="severityFilter" class="filter-select" size="small">
          <el-option label="全部严重度" value="all" />
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
        <el-select v-model="typeFilter" class="filter-select" size="small">
          <el-option label="全部类型" value="all" />
          <el-option v-for="item in crashTypeOptions.filter((x) => x !== 'all')" :key="item" :label="item" :value="item" />
        </el-select>
      </div>
    </div>

    <div v-if="filteredCrashes.length === 0" class="empty-state-card">当前报告未包含缺陷明细。</div>
    <div v-else class="defect-list">
      <article v-for="item in filteredCrashes" :key="item.id" class="defect-card" :class="{ 'new-defect-row': recentCrashIds.includes(item.id) }">
        <div class="defect-head">
          <span class="defect-id">{{ item.id || 'unknown-id' }}</span>
          <el-tag :type="severityTagType(item.severity)" :class="{ 'severity-low': normalizeSeverity(item.severity) === 'low' }">{{ severityLabel(item.severity) }}</el-tag>
          <span class="defect-type">{{ item.type || 'unknown' }}</span>
          <span class="defect-count">× {{ Number(item.occurrences || 1) }}</span>
          <span :class="verificationClass(item)">{{ verificationLabel(item) }}</span>
        </div>
        <div class="defect-meta-row">
          <span><b>位置:</b> {{ item.location || '--' }}</span>
          <span><b>风险:</b> {{ clipText(item.risk || item.description, 180) || '--' }}</span>
        </div>
        <div class="defect-section"><div class="defect-label">最小触发输入</div><pre class="defect-pre">{{ clipText(item.input, 420) || '--' }}</pre></div>
        <div class="defect-section"><div class="defect-label">调用栈 / 描述</div><pre class="defect-pre">{{ clipText(item.stackTrace, 900) || '--' }}</pre></div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.crash-section { display: flex; flex-direction: column; gap: 12px; }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.section-kicker { margin: 0 0 4px; color: #78a6d8; font-size: 12px; text-transform: uppercase; }
h2 { margin: 0; color: #eef7ff; font-size: 22px; }
.defect-toolbar { display: flex; gap: 8px; }
.filter-select { width: 170px; }
.empty-state-card { min-height: 160px; border: 1px dashed rgba(0,212,255,.28); border-radius: 10px; background: rgba(9,16,30,.64); color: #9ab2db; display: flex; align-items: center; justify-content: center; padding: 16px; }
.defect-list { display: flex; flex-direction: column; gap: 10px; }
.defect-card { border: 1px solid rgba(0,212,255,.16); border-radius: 10px; background: rgba(9,16,30,.72); padding: 12px; }
.defect-head { display: grid; grid-template-columns: minmax(120px,180px) 84px minmax(180px,1fr) 70px 76px; align-items: center; gap: 8px; margin-bottom: 8px; }
.defect-id, .defect-type { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.defect-id { color: #d7ebff; font-weight: 700; font-size: 12px; }
.defect-type, .defect-meta-row { color: #9eb7df; font-size: 12px; }
.defect-count { color: #83f3cf; font-size: 12px; font-weight: 600; text-align: center; }
.defect-meta-row { display: grid; grid-template-columns: minmax(180px,260px) 1fr; gap: 10px; }
.defect-meta-row b, .defect-label { color: #d8eaff; }
.defect-section { margin-top: 8px; }
.defect-label { font-size: 11px; margin-bottom: 4px; }
.defect-pre { margin: 0; padding: 8px; border-radius: 8px; background: rgba(5,10,20,.9); color: #c7dbff; font-size: 12px; line-height: 1.35; white-space: pre-wrap; word-break: break-all; }
.verify-ok { color: #34d399; font-weight: 600; }
.verify-bad { color: #f87171; font-weight: 600; }
.verify-static { color: #38bdf8; font-weight: 600; }
.severity-low { border-color: rgba(250,204,21,.45); background: rgba(250,204,21,.12); color: #facc15; }
.new-defect-row { animation: defectFlash 1s ease-in-out 2; }
@keyframes defectFlash { 50% { box-shadow: inset 0 0 0 999px rgba(255,87,87,.12); } }
@media (max-width: 900px) { .section-heading, .defect-meta-row { grid-template-columns: 1fr; flex-direction: column; align-items: stretch; } .defect-head { grid-template-columns: 1fr 70px; } }
</style>
