<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue'
import VChart from 'vue-echarts'
import { testStore } from '../stores/testStore'
import AnimatedNumber from './AnimatedNumber.vue'
import { fetchLatestReport } from '../api/runtimeControl'

const subTextColor = '#8ea2c9'
const axisLineColor = 'rgba(140, 164, 206, 0.3)'
let autoReportTimer = 0
let resizeTimer = 0

const hasLatestReport = computed(() => Boolean(testStore.latestReport?.report))
const latestReport = computed(() => testStore.latestReport?.report || {})
const summary = computed(() => latestReport.value?.summary || {})
const interactions = computed(() => latestReport.value?.agent_interactions || {})
const reportRoundCount = computed(() => Math.max(1, Number(interactions.value.messages_by_topic?.['cmd:generate'] || 1)))

const reportSummaryRows = computed(() => [
  { label: '生成用例', value: Number(summary.value.total_generated || testStore.metrics.totalCases || 0) },
  { label: '执行用例', value: Number(summary.value.total_executed || testStore.metrics.totalCases || 0) },
  { label: '通过用例', value: Number(summary.value.total_passed || testStore.metrics.approved || 0) },
  { label: '崩溃数', value: Number(summary.value.total_crashes || testStore.metrics.uniqueCrashes || 0) },
  { label: '确认缺陷', value: Number(summary.value.confirmed_bugs || testStore.metrics.uniqueCrashes || 0) },
  { label: '误报数', value: Number(summary.value.false_positives || testStore.metrics.falsePositives || 0) },
])

const latestLineCoverage = computed(() => Math.max(0, Math.min(100, Number(((Number(summary.value.line_coverage || 0)) * 100).toFixed(2)))))
const latestBranchCoverage = computed(() => Math.max(0, Math.min(100, Number(((Number(summary.value.branch_coverage || 0)) * 100).toFixed(2)))))

const lineCoverage = computed(() => Number(testStore.coverage.current || 0) || (hasLatestReport.value ? latestLineCoverage.value : 0))
const branchCoverage = computed(() => Number(testStore.coverage.branch || 0) || (hasLatestReport.value ? latestBranchCoverage.value : 0))
const totalLines = computed(() => Math.max(0, Math.round(Number(testStore.coverage.lineTotal || testStore.totalSourceLines || summary.value.total_source_lines || 0))))
const coveredLines = computed(() => {
  const covered = Number(testStore.coverage.lineCovered || summary.value.line_covered || 0)
  return covered > 0 ? Math.round(covered) : Math.round((lineCoverage.value / 100) * totalLines.value)
})

const effectiveCoverageHistory = computed(() => {
  const live = (testStore.coverage.history || []).filter((item) => Number(item?.round) > 0)
  if (live.length > 0) return live
  if (!hasLatestReport.value) return [{ round: 0, value: 0 }]
  const rounds = reportRoundCount.value
  return Array.from({ length: rounds }, (_, idx) => ({
    round: idx + 1,
    value: Number((((idx + 1) / rounds) * latestLineCoverage.value).toFixed(2)),
  }))
})

const rounds = computed(() => effectiveCoverageHistory.value.map((item) => item.round))
const coverageValues = computed(() => effectiveCoverageHistory.value.map((item) => Number(item.value || 0)))
const newCoverageLines = computed(() => effectiveCoverageHistory.value.map((item, index) => {
  if (index === 0) return 0
  const prev = Number(effectiveCoverageHistory.value[index - 1]?.value || 0)
  const current = Number(item?.value || 0)
  return Math.round((Math.max(0, current - prev) / 100) * totalLines.value)
}))

const phaseMarkLines = computed(() => {
  const marks = []
  const deepPoint = effectiveCoverageHistory.value.find((item) => Number(item.value || 0) >= 60)
  const finalizePoint = effectiveCoverageHistory.value.find((item) => Number(item.value || 0) >= 98)
  if (deepPoint) marks.push({ xAxis: deepPoint.round, name: 'DEEP', lineStyle: { color: '#f59e0b', type: 'dashed' }, label: { color: '#f59e0b', formatter: 'DEEP' } })
  if (finalizePoint) marks.push({ xAxis: finalizePoint.round, name: 'FINALIZE', lineStyle: { color: '#22c55e', type: 'dashed' }, label: { color: '#22c55e', formatter: 'FINALIZE' } })
  return marks
})

const coverageOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis', backgroundColor: 'rgba(12,18,33,0.95)', borderColor: 'rgba(0,212,255,0.25)', textStyle: { color: '#e8f3ff' } },
  legend: { top: 8, textStyle: { color: subTextColor }, data: ['覆盖率', '新增覆盖行'] },
  grid: { left: 52, right: 56, top: 46, bottom: 36 },
  xAxis: { type: 'category', data: rounds.value, axisLine: { lineStyle: { color: axisLineColor } }, axisLabel: { color: subTextColor } },
  yAxis: [
    { type: 'value', name: '覆盖率(%)', min: 0, max: 100, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { lineStyle: { color: 'rgba(120,140,170,0.12)' } }, axisLabel: { color: subTextColor }, nameTextStyle: { color: subTextColor } },
    { type: 'value', name: '新增覆盖行', min: 0, axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { show: false }, axisLabel: { color: subTextColor }, nameTextStyle: { color: subTextColor } },
  ],
  series: [
    { name: '覆盖率', type: 'line', smooth: true, symbol: 'circle', symbolSize: 6, itemStyle: { color: '#00d4ff' }, lineStyle: { width: 2.5, color: '#00d4ff' }, areaStyle: { color: 'rgba(0,212,255,0.16)' }, data: coverageValues.value, markLine: { symbol: 'none', label: { show: true, position: 'insideEndTop' }, data: phaseMarkLines.value } },
    { name: '新增覆盖行', type: 'bar', yAxisIndex: 1, barMaxWidth: 18, itemStyle: { color: 'rgba(0,255,136,0.55)' }, data: newCoverageLines.value },
  ],
}))

const lineRingStyle = computed(() => ({ background: `conic-gradient(#00d4ff ${lineCoverage.value}%, rgba(0,212,255,0.15) ${lineCoverage.value}% 100%)` }))
const branchRingStyle = computed(() => ({ background: `conic-gradient(#34d399 ${branchCoverage.value}%, rgba(52,211,153,0.16) ${branchCoverage.value}% 100%)` }))

function triggerChartResize() {
  window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(() => window.dispatchEvent(new Event('resize')), 180)
}

function ensureAutoReportPull() {
  if (autoReportTimer) return
  autoReportTimer = window.setInterval(async () => {
    if (testStore.status !== 'finished' || hasLatestReport.value) {
      window.clearInterval(autoReportTimer)
      autoReportTimer = 0
      return
    }
    const data = await fetchLatestReport(false)
    if (data?.report) triggerChartResize()
  }, 2000)
}

async function handleRefreshReport() {
  await fetchLatestReport(true)
  await nextTick()
  triggerChartResize()
}

watch(() => [hasLatestReport.value, testStore.latestReport?.fetchedAt], async () => {
  await nextTick()
  triggerChartResize()
})

watch(() => [testStore.status, hasLatestReport.value], ([status, hasReport]) => {
  if (status === 'finished' && !hasReport) ensureAutoReportPull()
}, { immediate: true })

onMounted(triggerChartResize)
onBeforeUnmount(() => {
  window.clearInterval(autoReportTimer)
  window.clearTimeout(resizeTimer)
})
</script>

<template>
  <section class="dashboard-section coverage-section">
    <div class="section-heading">
      <div>
        <p class="section-kicker">Coverage</p>
        <h2>覆盖率报告</h2>
      </div>
      <div class="report-status">
        <span class="report-dot" :class="hasLatestReport ? 'ok' : 'pending'"></span>
        <span>{{ hasLatestReport ? `报告已加载，执行 ${Number(summary.total_executed || 0)} 条` : '尚未加载报告' }}</span>
        <el-button size="small" type="primary" plain @click="handleRefreshReport">刷新报告</el-button>
      </div>
    </div>

    <div class="overview-grid">
      <div class="overview-card" v-for="item in reportSummaryRows" :key="item.label">
        <span>{{ item.label }}</span>
        <b><AnimatedNumber :value="item.value" /></b>
      </div>
    </div>

    <div class="coverage-rings">
      <div class="ring-card">
        <div class="ring-title">行覆盖率</div>
        <div class="mini-ring" :style="lineRingStyle"><div class="mini-ring-inner"><AnimatedNumber :value="lineCoverage" :decimals="2" suffix="%" /></div></div>
        <div class="ring-meta">{{ coveredLines }} / {{ totalLines }} 行</div>
      </div>
      <div class="ring-card">
        <div class="ring-title">分支覆盖率</div>
        <div class="mini-ring" :style="branchRingStyle"><div class="mini-ring-inner"><AnimatedNumber :value="branchCoverage" :decimals="2" suffix="%" /></div></div>
        <div class="ring-meta">{{ testStore.coverage.branchCovered }} / {{ testStore.coverage.branchTotal }} 分支</div>
      </div>
    </div>

    <div class="coverage-chart-card">
      <VChart class="coverage-chart" :option="coverageOption" autoresize />
    </div>
  </section>
</template>

<style scoped>
.coverage-section { display: flex; flex-direction: column; gap: 14px; }
.section-heading, .report-status, .overview-card, .coverage-rings { display: flex; align-items: center; }
.section-heading { justify-content: space-between; gap: 16px; }
.section-kicker { margin: 0 0 4px; color: #78a6d8; font-size: 12px; text-transform: uppercase; }
h2 { margin: 0; color: #eef7ff; font-size: 22px; }
.report-status { gap: 8px; color: #b9c9e8; font-size: 12px; }
.report-dot { width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; box-shadow: 0 0 8px rgba(245,158,11,.7); }
.report-dot.ok { background: #22c55e; box-shadow: 0 0 8px rgba(34,197,94,.7); }
.overview-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; }
.overview-card { justify-content: space-between; padding: 12px; border: 1px solid rgba(0,212,255,.16); border-radius: 8px; background: rgba(6,12,24,.72); color: #91a9d2; }
.overview-card b { color: #e3f5ff; font-size: 20px; }
.coverage-rings { gap: 14px; }
.ring-card { flex: 1; padding: 18px; border: 1px solid rgba(0,212,255,.16); border-radius: 10px; background: rgba(10,18,33,.72); text-align: center; }
.ring-title { color: #dcecff; font-size: 14px; margin-bottom: 12px; }
.mini-ring { width: 150px; height: 150px; margin: 0 auto; border-radius: 50%; padding: 10px; }
.mini-ring-inner { width: 100%; height: 100%; border-radius: 50%; background: rgba(8,14,26,.95); color: #e8f5ff; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: 700; }
.ring-meta { margin-top: 10px; color: #8fa7d3; font-size: 12px; }
.coverage-chart-card { min-height: 400px; padding: 10px; border: 1px solid rgba(0,212,255,.16); border-radius: 10px; background: rgba(10,18,33,.72); }
.coverage-chart { width: 100%; height: 400px; }
@media (max-width: 1100px) { .overview-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .coverage-rings, .section-heading { flex-direction: column; align-items: stretch; } }
</style>
