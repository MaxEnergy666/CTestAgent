<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElNotification } from 'element-plus'
import HeaderBar from './components/HeaderBar.vue'
import LeftPanel from './components/LeftPanel.vue'
import AgentFlowView from './components/AgentFlowView.vue'
import RightPanel from './components/RightPanel.vue'
import CoverageReportSection from './components/CoverageReportSection.vue'
import CrashListSection from './components/CrashListSection.vue'
import AgentLogSection from './components/AgentLogSection.vue'
import StatsChartsSection from './components/StatsChartsSection.vue'
import AnimatedNumber from './components/AnimatedNumber.vue'
import { testStore } from './stores/testStore'

const paramsSectionRef = ref(null)
const crashFlash = ref(false)
let crashFlashTimer = null

const reportSummary = computed(() => testStore.latestReport?.report?.summary || {})

const statCards = computed(() => {
  const summary = reportSummary.value
  const generated = Number(summary.total_generated ?? testStore.metrics.totalCases + testStore.metrics.rejected)
  const executed = Number(summary.total_executed ?? testStore.metrics.totalCases)
  const passed = Number(summary.total_passed ?? testStore.metrics.approved)
  const confirmed = Number(summary.confirmed_bugs ?? testStore.metrics.uniqueCrashes)
  const falsePositives = Number(summary.false_positives ?? testStore.metrics.falsePositives)

  return [
    { label: '生成用例', value: generated, tone: 'cyan' },
    { label: '执行用例', value: executed, tone: 'blue' },
    { label: '通过用例', value: passed, tone: 'green' },
    { label: '崩溃数', value: testStore.crashes.length, tone: 'red' },
    { label: '确认缺陷', value: confirmed, tone: 'orange' },
    { label: '误报数', value: falsePositives, tone: 'violet' },
  ]
})

const strategySummary = computed(() => {
  const weights = testStore.config.strategyWeights || {}
  const items = [
    ['随机', weights.random],
    ['边界', weights.boundary],
    ['语法', weights.grammar],
    ['覆盖', weights.coverageGuided],
  ]

  return items
    .map(([label, value]) => `${label} ${Math.round(Number(value || 0) * 100)}%`)
    .join(' / ')
})

const runtimeSummary = computed(() => {
  return [
    `轮次上限 ${testStore.config.maxRounds}`,
    `单例超时 ${testStore.config.timeout}s`,
    `时间上限 ${testStore.config.maxTime}min`,
  ].join(' / ')
})

function scrollToParams() {
  paramsSectionRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

watch(
  () => testStore.crashes.length,
  (next, prev) => {
    if (next <= prev) return
    const latest = testStore.crashes[0]

    crashFlash.value = true
    if (crashFlashTimer) window.clearTimeout(crashFlashTimer)
    crashFlashTimer = window.setTimeout(() => {
      crashFlash.value = false
    }, 360)

    ElNotification({
      title: '发现新缺陷',
      message: `${latest?.type || 'unknown'}（${latest?.id || 'N/A'}）`,
      type: 'error',
      duration: 2200,
      position: 'bottom-right',
    })
  },
)

onBeforeUnmount(() => {
  if (crashFlashTimer) {
    window.clearTimeout(crashFlashTimer)
    crashFlashTimer = null
  }
})
</script>

<template>
  <div class="app-layout" :class="{ 'crash-flash': crashFlash }">
    <HeaderBar class="layout-header" />

    <main class="dashboard-main">
      <section class="dashboard-section control-section">
        <div class="section-heading control-heading">
          <div>
            <span class="eyebrow">准备测试</span>
            <h2>源码上传与快速启动</h2>
          </div>
        </div>

        <div class="control-grid">
          <div class="upload-card">
            <LeftPanel />
          </div>

          <div class="param-summary-card">
            <div class="summary-title">
              <span>当前关键参数</span>
              <el-button text type="primary" @click="scrollToParams">修改参数</el-button>
            </div>
            <dl class="param-summary-list">
              <div>
                <dt>策略权重</dt>
                <dd>{{ strategySummary }}</dd>
              </div>
              <div>
                <dt>执行参数</dt>
                <dd>{{ runtimeSummary }}</dd>
              </div>
              <div>
                <dt>Reviewer</dt>
                <dd>阈值 {{ Math.round(Number(testStore.config.reviewerThreshold || 0) * 100) }}% / 复核 {{ testStore.config.crashVerifyCount }} 次</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section class="dashboard-section agent-section">
        <div class="section-heading">
          <div>
            <span class="eyebrow">多智能体协作</span>
            <h2>中央主视图</h2>
          </div>
        </div>
        <div class="agent-stage">
          <AgentFlowView />
        </div>
      </section>

      <section class="stats-row" aria-label="实时统计">
        <div v-for="card in statCards" :key="card.label" class="stat-card" :class="`tone-${card.tone}`">
          <span>{{ card.label }}</span>
          <b><AnimatedNumber :value="card.value" /></b>
        </div>
      </section>

      <CoverageReportSection />
      <CrashListSection />
      <AgentLogSection />
      <StatsChartsSection />

      <section ref="paramsSectionRef" class="dashboard-section params-section">
        <div class="section-heading">
          <div>
            <span class="eyebrow">集中配置</span>
            <h2>测试参数控制面板</h2>
          </div>
        </div>
        <RightPanel />
      </section>
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
  background:
    linear-gradient(rgba(0, 212, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 212, 255, 0.045) 1px, transparent 1px),
    linear-gradient(160deg, #0a0e1a 0%, #111827 100%);
  background-size: 36px 36px, 36px 36px, auto;
  color: #e8efff;
}

.layout-header {
  position: sticky;
  top: 0;
  z-index: 30;
  min-height: 60px;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.28);
}

.dashboard-main {
  width: min(1520px, calc(100% - 32px));
  margin: 0 auto;
  padding: 16px 0 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.dashboard-section,
.stats-row {
  border: 1px solid rgba(0, 212, 255, 0.2);
  border-radius: 12px;
  background: rgba(8, 15, 30, 0.72);
  box-shadow:
    inset 0 0 0 1px rgba(0, 212, 255, 0.08),
    0 10px 24px rgba(0, 0, 0, 0.22);
  backdrop-filter: blur(8px);
}

.dashboard-section {
  padding: 16px;
}

.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 14px;
}

.section-heading h2 {
  margin: 2px 0 0;
  color: #f3f8ff;
  font-size: 18px;
  font-weight: 700;
}

.eyebrow {
  color: #69ddff;
  font-size: 12px;
  letter-spacing: 0;
}

.control-grid {
  display: grid;
  grid-template-columns: minmax(360px, 0.95fr) minmax(420px, 1.05fr);
  gap: 14px;
  align-items: stretch;
}

.upload-card,
.param-summary-card {
  min-height: 100%;
  border: 1px solid rgba(0, 212, 255, 0.14);
  border-radius: 10px;
  background: rgba(10, 18, 34, 0.72);
}

.upload-card {
  padding: 0;
}

.param-summary-card {
  padding: 16px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.summary-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  color: #f3f8ff;
  font-weight: 700;
}

.param-summary-list {
  margin: 12px 0 0;
  display: grid;
  gap: 10px;
}

.param-summary-list div {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
}

.param-summary-list dt {
  color: #7fb2d2;
  font-size: 12px;
}

.param-summary-list dd {
  margin: 0;
  color: #dbeafe;
  line-height: 1.5;
}

.agent-stage {
  height: 500px;
  min-height: 500px;
}

.stats-row {
  padding: 14px;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

.stat-card {
  min-height: 96px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  background: rgba(12, 22, 42, 0.86);
  padding: 14px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.stat-card span {
  color: #9fb2d7;
  font-size: 13px;
}

.stat-card b {
  color: #f8fbff;
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
}

.tone-cyan {
  border-color: rgba(34, 211, 238, 0.28);
}

.tone-blue {
  border-color: rgba(96, 165, 250, 0.28);
}

.tone-green {
  border-color: rgba(74, 222, 128, 0.28);
}

.tone-red {
  border-color: rgba(248, 113, 113, 0.28);
}

.tone-orange {
  border-color: rgba(251, 146, 60, 0.28);
}

.tone-violet {
  border-color: rgba(167, 139, 250, 0.28);
}

.params-section {
  scroll-margin-top: 78px;
}

.crash-flash {
  animation: crashScreenFlash 0.34s ease;
}

@keyframes crashScreenFlash {
  0% {
    box-shadow: inset 0 0 0 0 rgba(239, 68, 68, 0);
  }
  45% {
    box-shadow: inset 0 0 0 2000px rgba(239, 68, 68, 0.16);
  }
  100% {
    box-shadow: inset 0 0 0 0 rgba(239, 68, 68, 0);
  }
}

@media (max-width: 1180px) {
  .dashboard-main {
    width: min(100% - 20px, 1520px);
  }

  .control-grid {
    grid-template-columns: 1fr;
  }

  .stats-row {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .dashboard-main {
    width: min(100% - 14px, 1520px);
    padding-top: 10px;
  }

  .dashboard-section,
  .stats-row {
    border-radius: 10px;
  }

  .control-heading,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .stats-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .agent-stage {
    height: 460px;
    min-height: 460px;
  }
}
</style>
