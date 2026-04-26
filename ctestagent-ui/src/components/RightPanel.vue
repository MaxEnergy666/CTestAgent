<script setup>
import { computed, ref, watch } from 'vue'
import { testStore } from '../stores/testStore'
import { Operation, Setting, Switch, Histogram } from '@element-plus/icons-vue'

const strategyProfiles = [
  {
    label: '平衡模式（默认）',
    value: 'balanced',
    weights: { random: 0.2, boundary: 0.35, grammar: 0.35, coverageGuided: 0.1 },
  },
  {
    label: '探索模式（随机+边界）',
    value: 'explore',
    weights: { random: 0.35, boundary: 0.35, grammar: 0.2, coverageGuided: 0.1 },
  },
  {
    label: '边界优先',
    value: 'boundary_first',
    weights: { random: 0.15, boundary: 0.55, grammar: 0.2, coverageGuided: 0.1 },
  },
  {
    label: '覆盖引导优先',
    value: 'coverage_first',
    weights: { random: 0.15, boundary: 0.2, grammar: 0.25, coverageGuided: 0.4 },
  },
]

const reviewerProfiles = [
  {
    label: '平衡审查',
    value: 'balanced',
    weights: { diversity: 0.3, coverage: 0.4, validity: 0.3 },
  },
  {
    label: '覆盖优先审查',
    value: 'coverage_first',
    weights: { diversity: 0.2, coverage: 0.6, validity: 0.2 },
  },
  {
    label: '严格有效性审查',
    value: 'validity_first',
    weights: { diversity: 0.2, coverage: 0.25, validity: 0.55 },
  },
]

const selectedStrategyProfile = ref('balanced')
const selectedReviewerProfile = ref('balanced')

const batchSizeOptions = [10, 20, 30, 50, 80]
const timeoutOptions = [1, 2, 3, 5, 8, 10]
const maxRoundsOptions = [50, 100, 200, 500, 800, 1000]
const maxTimeOptions = [5, 10, 20, 30, 60, 120]
const reviewerThresholdOptions = [0.3, 0.4, 0.5, 0.6]
const crashVerifyOptions = [1, 2, 3, 4, 5]
const phaseOptions = [
  { label: 'EXPLORE（探索）', value: 'EXPLORE' },
  { label: 'DEEP（深度）', value: 'DEEP' },
]
const autoPhaseThresholdOptions = [40, 50, 60, 70, 80]

watch(
  selectedStrategyProfile,
  (value) => {
    const hit = strategyProfiles.find((item) => item.value === value)
    if (!hit) return
    Object.assign(testStore.config.strategyWeights, hit.weights)
  },
  { immediate: true },
)

watch(
  selectedReviewerProfile,
  (value) => {
    const hit = reviewerProfiles.find((item) => item.value === value)
    if (!hit) return
    Object.assign(testStore.config.reviewerWeights, hit.weights)
  },
  { immediate: true },
)

watch(
  () => testStore.config.maxRounds,
  (value) => {
    const rounds = Number(value || 0)
    if (Number.isFinite(rounds) && rounds > 0) {
      testStore.maxRounds = rounds
      testStore.config.maxRoundsHardCap = Math.max(Number(testStore.config.maxRoundsHardCap || 0), rounds)
    }
  },
  { immediate: true },
)

const phaseTagType = computed(() => {
  if (testStore.phase === 'EXPLORE') return 'primary'
  if (testStore.phase === 'DEEP') return 'warning'
  if (testStore.phase === 'FINALIZE') return 'success'
  return 'info'
})

const strategyPreview = computed(() => {
  const w = testStore.config.strategyWeights
  return `random ${Math.round(w.random * 100)}% / boundary ${Math.round(w.boundary * 100)}% / grammar ${Math.round(w.grammar * 100)}% / coverage ${Math.round(w.coverageGuided * 100)}%`
})

const reviewerPreview = computed(() => {
  const w = testStore.config.reviewerWeights
  return `diversity ${Math.round(w.diversity * 100)}% / coverage ${Math.round(w.coverage * 100)}% / validity ${Math.round(w.validity * 100)}%`
})
</script>

<template>
  <div class="right-panel-inner">
    <el-collapse>
      <!-- 区域1：策略权重（下拉） -->
      <el-collapse-item name="strategy" title="策略权重">
        <template #title>
          <div class="collapse-title">
            <el-icon><Operation /></el-icon>
            <span>策略权重</span>
          </div>
        </template>

        <div class="section-content">
          <div class="select-item">
            <span>策略方案</span>
            <el-select v-model="selectedStrategyProfile" class="select-control" size="small">
              <el-option v-for="item in strategyProfiles" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>
          <div class="preview-box">
            <span>当前权重</span>
            <b>{{ strategyPreview }}</b>
          </div>
        </div>
      </el-collapse-item>

      <!-- 区域2：执行参数（下拉） -->
      <el-collapse-item name="runtime">
        <template #title>
          <div class="collapse-title">
            <el-icon><Setting /></el-icon>
            <span>执行参数</span>
          </div>
        </template>

        <div class="section-content">
          <div class="select-item">
            <span>每轮批次大小</span>
            <el-select v-model="testStore.config.batchSize" class="select-control" size="small">
              <el-option v-for="item in batchSizeOptions" :key="`batch_${item}`" :label="`${item}`" :value="item" />
            </el-select>
          </div>

          <div class="select-item">
            <span>单用例超时（秒）</span>
            <el-select v-model="testStore.config.timeout" class="select-control" size="small">
              <el-option v-for="item in timeoutOptions" :key="`timeout_${item}`" :label="`${item}`" :value="item" />
            </el-select>
          </div>

          <div class="select-item">
            <span>轮次硬上限</span>
            <el-select v-model="testStore.config.maxRounds" class="select-control" size="small">
              <el-option v-for="item in maxRoundsOptions" :key="`round_${item}`" :label="`${item}`" :value="item" />
            </el-select>
          </div>

          <div class="select-item">
            <span>最大运行时间（分钟）</span>
            <el-select v-model="testStore.config.maxTime" class="select-control" size="small">
              <el-option v-for="item in maxTimeOptions" :key="`max_time_${item}`" :label="`${item}`" :value="item" />
            </el-select>
          </div>
        </div>
      </el-collapse-item>

      <!-- 区域3：Reviewer 配置（下拉） -->
      <el-collapse-item name="reviewer">
        <template #title>
          <div class="collapse-title">
            <el-icon><Histogram /></el-icon>
            <span>Reviewer 配置</span>
          </div>
        </template>

        <div class="section-content">
          <div class="select-item">
            <span>审查通过阈值</span>
            <el-select v-model="testStore.config.reviewerThreshold" class="select-control" size="small">
              <el-option v-for="item in reviewerThresholdOptions" :key="`thr_${item}`" :label="`${Math.round(item * 100)}%`" :value="item" />
            </el-select>
          </div>

          <div class="select-item">
            <span>崩溃验证次数</span>
            <el-select v-model="testStore.config.crashVerifyCount" class="select-control" size="small">
              <el-option v-for="item in crashVerifyOptions" :key="`verify_${item}`" :label="`${item}`" :value="item" />
            </el-select>
          </div>

          <div class="select-item">
            <span>审查权重方案</span>
            <el-select v-model="selectedReviewerProfile" class="select-control" size="small">
              <el-option v-for="item in reviewerProfiles" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>
          <div class="preview-box">
            <span>当前审查权重</span>
            <b>{{ reviewerPreview }}</b>
          </div>
        </div>
      </el-collapse-item>

      <!-- 区域4：阶段切换（下拉） -->
      <el-collapse-item name="phase-switch">
        <template #title>
          <div class="collapse-title">
            <el-icon><Switch /></el-icon>
            <span>阶段切换</span>
          </div>
        </template>

        <div class="section-content">
          <div class="phase-box">
            <div class="phase-label">当前阶段</div>
            <el-tag :type="phaseTagType" effect="dark" class="phase-tag">{{ testStore.phase }}</el-tag>
          </div>

          <div class="select-item">
            <span>初始阶段</span>
            <el-select v-model="testStore.phase" class="select-control" size="small">
              <el-option v-for="item in phaseOptions" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>

          <div class="select-item">
            <span>自动切换阈值</span>
            <el-select v-model="testStore.config.autoPhaseCoverageThreshold" class="select-control" size="small">
              <el-option
                v-for="item in autoPhaseThresholdOptions"
                :key="`phase_thr_${item}`"
                :label="`${item}%`"
                :value="item"
              />
            </el-select>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.right-panel-inner {
  min-height: 100%;
  padding: 10px;
  background: rgba(15, 20, 35, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}

:deep(.el-collapse) {
  border: none;
  --el-collapse-header-bg-color: transparent;
  --el-collapse-content-bg-color: transparent;
  --el-collapse-border-color: rgba(0, 212, 255, 0.15);
}

:deep(.el-collapse-item__header) {
  color: #e8f3ff;
  border-bottom-color: rgba(0, 212, 255, 0.15);
  font-weight: 600;
}

:deep(.el-collapse-item__wrap) {
  border-bottom-color: rgba(0, 212, 255, 0.15);
}

.collapse-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.section-content {
  padding: 8px 2px 4px;
}

.select-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}

.select-item > span {
  color: #d3e2fc;
  font-size: 12px;
  flex: 1;
}

.select-control {
  width: 170px;
}

.preview-box {
  margin-top: 4px;
  border: 1px solid rgba(0, 212, 255, 0.2);
  border-radius: 8px;
  background: rgba(8, 16, 30, 0.72);
  padding: 8px;
  color: #97b1d9;
  font-size: 12px;
  line-height: 1.45;
}

.preview-box b {
  display: block;
  margin-top: 4px;
  color: #d9edff;
  font-weight: 600;
}

.phase-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.phase-label {
  color: #d6e5ff;
  font-size: 13px;
}

.phase-tag {
  font-size: 14px;
  font-weight: 700;
}
</style>
