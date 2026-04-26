<script setup>
import { computed } from 'vue'
import { VideoPlay, VideoPause, SwitchButton } from '@element-plus/icons-vue'
import { testStore } from '../stores/testStore'
import { pauseTestFlow, startTestFlow, stopTestFlow } from '../api/runtimeControl'

const phaseLabel = computed(() => testStore.phase)

const phaseClass = computed(() => {
  if (testStore.phase === 'EXPLORE') return 'phase-explore'
  if (testStore.phase === 'DEEP') return 'phase-deep'
  if (testStore.phase === 'FINALIZE') return 'phase-finalize'
  return 'phase-init'
})

const runtimeText = computed(() => {
  const total = Number(testStore.elapsedSeconds || 0)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const pad = (value) => String(value).padStart(2, '0')
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
})

const statusText = computed(() => {
  if (testStore.status === 'running') return '运行中'
  if (testStore.status === 'error') return '出错'
  if (testStore.status === 'paused') return '已暂停'
  if (testStore.status === 'finished') return '已完成'
  return '空闲'
})

const statusClass = computed(() => {
  if (testStore.status === 'running') return 'status-running'
  if (testStore.status === 'error') return 'status-error'
  return 'status-idle'
})

const stopReasonLabel = computed(() => {
  const reason = String(testStore.stopReason || '').trim()
  if (!reason) return ''
  if (reason === 'coverage_goal_reached') return '覆盖目标达成'
  if (reason === 'no_progress_round_limit') return '连续无进展提前停止'
  if (reason === 'coverage_stagnation') return '覆盖率停滞提前停止'
  if (reason === 'time_budget_exhausted') return '达到时间上限'
  if (reason === 'round_hard_cap_reached') return '达到轮次硬上限'
  if (reason === 'executor_compile_failed') return '执行器编译失败，已提前结束'
  if (reason === 'manual_stop') return '手动停止'
  return reason
})

const errorSummary = computed(() => {
  const text = String(testStore.lastError || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  if (text.length <= 88) return text
  return `${text.slice(0, 88)}...`
})

const startButtonDisabled = computed(() => {
  return testStore.status === 'running' || testStore.uploadingSource || testStore.startingTest
})

const startButtonLoading = computed(() => testStore.uploadingSource || testStore.startingTest)

async function handleStart() {
  try {
    await startTestFlow()
  } catch (error) {
    // 详细提示已在 runtimeControl 内处理
    console.error('handleStart error:', error)
  }
}

async function handlePause() {
  try {
    await pauseTestFlow()
  } catch (error) {
    console.error('handlePause error:', error)
  }
}

async function handleStop() {
  try {
    await stopTestFlow(true)
  } catch (error) {
    console.error('handleStop error:', error)
  }
}

</script>

<template>
  <header class="header-bar">
    <!-- 左侧：Logo + 名称 -->
    <div class="header-left">
      <div class="logo-icon">CT</div>
      <div class="title-group">
        <h1 class="title">CTestAgent 多智能体测试系统</h1>
      </div>
    </div>

    <!-- 中间：阶段 + 轮次 + 运行时间 -->
    <div class="header-center">
      <el-tag :class="['phase-tag', phaseClass]" effect="dark">{{ phaseLabel }}</el-tag>
      <span class="meta-text">Round {{ testStore.currentRound }}/{{ testStore.maxRounds }}</span>
      <span class="meta-text">运行时间 {{ runtimeText }}</span>
      <span v-if="statusText === '已完成' && stopReasonLabel" class="meta-text stop-reason">停止原因 {{ stopReasonLabel }}</span>
      <span v-if="(statusText === '已完成' || statusText === '出错') && errorSummary" class="meta-text error-reason" :title="testStore.lastError">错误 {{ errorSummary }}</span>
    </div>

    <!-- 右侧：状态灯 + 控制按钮 -->
    <div class="header-right">
      <div class="status-wrap">
        <span :class="['status-dot', statusClass]"></span>
        <span class="status-text">{{ statusText }}</span>
      </div>

      <div class="action-group">
        <el-button class="glow-btn" type="primary" round :disabled="startButtonDisabled" :loading="startButtonLoading" @click="handleStart">
          <el-icon><VideoPlay /></el-icon>
          启动
        </el-button>
        <el-button class="glow-btn" round @click="handlePause">
          <el-icon><VideoPause /></el-icon>
          暂停
        </el-button>
        <el-button class="glow-btn danger-btn" round @click="handleStop">
          <el-icon><SwitchButton /></el-icon>
          停止
        </el-button>
      </div>
    </div>
  </header>
</template>

<style scoped>
.header-bar {
  height: 60px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 16px;
  padding: 0 16px;
  background: linear-gradient(90deg, #0a0e1a 0%, #111827 100%);
  border-bottom: 1px solid rgba(0, 212, 255, 0.25);
  box-shadow: 0 2px 16px rgba(0, 212, 255, 0.12);
}

.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.logo-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: #00131f;
  background: #00d4ff;
  box-shadow: 0 0 14px rgba(0, 212, 255, 0.45);
}

.title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #f4f8ff;
  white-space: nowrap;
}

.header-center {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  min-width: 0;
}

.phase-tag {
  border: none;
  font-weight: 600;
}

.phase-init {
  background: #4b5563;
}

.phase-explore {
  background: #3b82f6;
}

.phase-deep {
  background: #f59e0b;
}

.phase-finalize {
  background: #22c55e;
}

.meta-text {
  color: #c8d4ef;
  font-size: 13px;
  white-space: nowrap;
}

.stop-reason {
  color: #9ff0ce;
}

.error-reason {
  color: #ffb4b4;
  max-width: 420px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.status-running {
  background: #22c55e;
  box-shadow: 0 0 10px rgba(34, 197, 94, 0.75);
}

.status-idle {
  background: #9ca3af;
  box-shadow: 0 0 8px rgba(156, 163, 175, 0.45);
}

.status-error {
  background: #ef4444;
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.75);
}

.status-text {
  color: #d8e3fb;
  font-size: 13px;
}

.action-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.glow-btn {
  border-color: rgba(0, 212, 255, 0.45);
  background: rgba(0, 212, 255, 0.08);
  color: #d8f7ff;
  box-shadow: 0 0 10px rgba(0, 212, 255, 0.12);
}

.glow-btn:hover {
  border-color: rgba(0, 212, 255, 0.8);
  box-shadow: 0 0 14px rgba(0, 212, 255, 0.25);
}

.danger-btn {
  border-color: rgba(248, 113, 113, 0.45);
  background: rgba(248, 113, 113, 0.08);
  color: #ffd9d9;
}

@media (max-width: 1400px) {
  .meta-text:nth-child(3) {
    display: none;
  }
}
</style>
