<script setup>
import { computed, nextTick, onMounted, watch } from 'vue'
import VChart from 'vue-echarts'
import { testStore } from '../stores/testStore'

const baseTextColor = '#d6e4ff'
const subTextColor = '#8ea2c9'
const axisLineColor = 'rgba(140, 164, 206, 0.3)'
const agents = ['Coordinator', 'Analyzer', 'Generator', 'Executor', 'Reviewer', 'Reporter']

const latestReport = computed(() => testStore.latestReport?.report || {})
const interactions = computed(() => latestReport.value?.agent_interactions || {})
const summary = computed(() => latestReport.value?.summary || {})
const senderRows = computed(() => Object.entries(interactions.value.messages_by_sender || {}).map(([name, count]) => ({ name, count: Number(count || 0) })))
const topicRows = computed(() => Object.entries(interactions.value.messages_by_topic || {}).map(([topic, count]) => ({ topic, count: Number(count || 0) })))

const effectiveCrashes = computed(() => {
  const live = Array.isArray(testStore.crashes) ? testStore.crashes : []
  if (live.length > 0) return live
  return (latestReport.value?.bugs || []).map((bug, index) => ({
    id: bug.id || `bug_${index}`,
    type: bug.type || 'unknown',
    severity: String(bug.risk_level || bug.severity || 'medium').toLowerCase(),
  }))
})

const effectiveMessages = computed(() => {
  const live = Array.isArray(testStore.messages) ? testStore.messages : []
  if (live.length > 0) return live
  return topicRows.value.map((item) => ({ from: 'Report', to: 'Dashboard', type: item.topic, count: item.count }))
})

const summaryRows = computed(() => [
  { label: '生成用例', value: Number(summary.value.total_generated || testStore.metrics.totalCases || 0) },
  { label: '执行用例', value: Number(summary.value.total_executed || testStore.metrics.totalCases || 0) },
  { label: '通过用例', value: Number(summary.value.total_passed || testStore.metrics.approved || 0) },
  { label: '崩溃数', value: Number(summary.value.total_crashes || testStore.metrics.uniqueCrashes || 0) },
  { label: '确认缺陷', value: Number(summary.value.confirmed_bugs || testStore.metrics.uniqueCrashes || 0) },
  { label: '误报数', value: Number(summary.value.false_positives || testStore.metrics.falsePositives || 0) },
])

const senderBarOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  grid: { left: 42, right: 12, top: 28, bottom: 42 },
  xAxis: { type: 'category', data: senderRows.value.map((item) => item.name), axisLine: { lineStyle: { color: axisLineColor } }, axisLabel: { color: subTextColor, fontSize: 11 } },
  yAxis: { type: 'value', axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { lineStyle: { color: 'rgba(120,140,170,0.12)' } }, axisLabel: { color: subTextColor, fontSize: 11 } },
  series: [{ name: '消息数', type: 'bar', barWidth: '45%', itemStyle: { color: '#38bdf8' }, data: senderRows.value.map((item) => item.count) }],
}))

const topicPieOption = computed(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'item' },
  legend: { bottom: 0, textStyle: { color: subTextColor, fontSize: 10 } },
  series: [{ type: 'pie', radius: ['35%', '68%'], center: ['50%', '42%'], label: { color: baseTextColor, fontSize: 10 }, data: topicRows.value.map((item) => ({ name: item.topic, value: item.count })), itemStyle: { borderColor: '#0a1020', borderWidth: 1 } }],
}))

const defectBarOption = computed(() => {
  const map = {}
  effectiveCrashes.value.forEach((item) => { map[item.type || 'unknown'] = (map[item.type || 'unknown'] || 0) + 1 })
  const xData = Object.keys(map)
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 42, right: 14, top: 30, bottom: 92 },
    xAxis: { type: 'category', data: xData, axisLine: { lineStyle: { color: axisLineColor } }, axisLabel: { color: subTextColor, fontSize: 10, rotate: 20, interval: 0, hideOverlap: true, formatter: (value) => (String(value).length <= 28 ? value : `${String(value).slice(0, 25)}...`) } },
    yAxis: { type: 'value', axisLine: { lineStyle: { color: axisLineColor } }, splitLine: { lineStyle: { color: 'rgba(120,140,170,0.12)' } }, axisLabel: { color: subTextColor, fontSize: 11 } },
    series: [{ type: 'bar', barWidth: '45%', itemStyle: { color: '#f97316' }, data: xData.map((key) => map[key]) }],
  }
})

const interactionHeatmapOption = computed(() => {
  const idxMap = Object.fromEntries(agents.map((name, idx) => [name, idx]))
  const matrix = Array.from({ length: agents.length }, () => Array.from({ length: agents.length }, () => 0))
  effectiveMessages.value.forEach((msg) => {
    const i = idxMap[msg.from]
    const j = idxMap[msg.to]
    if (i !== undefined && j !== undefined) matrix[i][j] += Number(msg.count || 1)
  })
  const data = []
  let maxVal = 1
  for (let i = 0; i < agents.length; i += 1) {
    for (let j = 0; j < agents.length; j += 1) {
      maxVal = Math.max(maxVal, matrix[i][j])
      data.push([j, i, matrix[i][j]])
    }
  }
  return {
    backgroundColor: 'transparent',
    tooltip: { formatter: (params) => `${agents[params.data[1]]} → ${agents[params.data[0]]}<br/>交互次数：${params.data[2]}` },
    grid: { left: 70, right: 20, top: 20, bottom: 52 },
    xAxis: { type: 'category', data: agents, axisLabel: { color: subTextColor, fontSize: 10, rotate: 20 }, axisLine: { lineStyle: { color: axisLineColor } }, splitArea: { show: true, areaStyle: { color: ['rgba(10,18,33,0.35)', 'rgba(12,22,39,0.22)'] } } },
    yAxis: { type: 'category', data: agents, axisLabel: { color: subTextColor, fontSize: 10 }, axisLine: { lineStyle: { color: axisLineColor } }, splitArea: { show: true, areaStyle: { color: ['rgba(10,18,33,0.35)', 'rgba(12,22,39,0.22)'] } } },
    visualMap: { min: 0, max: maxVal, calculable: true, orient: 'horizontal', left: 'center', bottom: 8, textStyle: { color: subTextColor }, inRange: { color: ['#0f2238', '#155273', '#00d4ff'] } },
    series: [{ type: 'heatmap', data, label: { show: true, color: '#eaf4ff', fontSize: 9 }, emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,212,255,0.4)' } } }],
  }
})

function resizeCharts() {
  window.dispatchEvent(new Event('resize'))
}

watch(() => [testStore.latestReport?.fetchedAt, testStore.messages.length, testStore.crashes.length], async () => {
  await nextTick()
  resizeCharts()
})
onMounted(resizeCharts)
</script>

<template>
  <section class="dashboard-section stats-section">
    <div class="section-heading">
      <p class="section-kicker">Analytics</p>
      <h2>统计图表</h2>
    </div>
    <div class="stats-summary-grid">
      <div class="stats-kv" v-for="item in summaryRows" :key="item.label"><span>{{ item.label }}</span><b>{{ item.value }}</b></div>
    </div>
    <div class="stats-grid">
      <div class="chart-card"><div class="chart-title">发送方消息数量</div><VChart class="chart" :option="senderBarOption" autoresize /></div>
      <div class="chart-card"><div class="chart-title">主题分布占比</div><VChart class="chart" :option="topicPieOption" autoresize /></div>
      <div class="chart-card"><div class="chart-title">缺陷类型分布</div><VChart class="chart" :option="defectBarOption" autoresize /></div>
      <div class="chart-card"><div class="chart-title">智能体交互热力图</div><VChart class="chart" :option="interactionHeatmapOption" autoresize /></div>
    </div>
  </section>
</template>

<style scoped>
.stats-section { display: flex; flex-direction: column; gap: 12px; }
.section-kicker { margin: 0 0 4px; color: #78a6d8; font-size: 12px; text-transform: uppercase; }
h2 { margin: 0; color: #eef7ff; font-size: 22px; }
.stats-summary-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.stats-kv { border: 1px solid rgba(0,212,255,.16); border-radius: 8px; background: rgba(9,16,30,.7); padding: 8px; color: #9ab2db; display: flex; align-items: center; justify-content: space-between; font-size: 12px; }
.stats-kv b { color: #dff4ff; }
.stats-grid { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: repeat(2, minmax(260px, auto)); gap: 10px; }
.chart-card { display: flex; flex-direction: column; padding: 10px; min-height: 260px; border: 1px solid rgba(0,212,255,.16); border-radius: 10px; background: rgba(10,18,33,.72); }
.chart-title { color: #d8e8ff; font-size: 12px; margin-bottom: 2px; }
.chart { width: 100%; flex: 1; min-height: 230px; }
@media (max-width: 1100px) { .stats-summary-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .stats-grid { grid-template-columns: 1fr; } }
</style>
