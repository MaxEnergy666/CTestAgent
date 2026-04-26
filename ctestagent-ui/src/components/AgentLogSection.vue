<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { testStore } from '../stores/testStore'

const logTypeFilters = ref(['cmd', 'data', 'feedback'])
const logContainerRef = ref(null)
const autoScrollEnabled = ref(true)

const latestReport = computed(() => testStore.latestReport?.report || {})
const interactions = computed(() => latestReport.value?.agent_interactions || {})
const topicRows = computed(() => Object.entries(interactions.value.messages_by_topic || {}).map(([topic, count]) => ({ topic, count: Number(count || 0) })))
const senderRows = computed(() => Object.entries(interactions.value.messages_by_sender || {}).map(([name, count]) => ({ name, count: Number(count || 0) })))

const fallbackMessagesFromReport = computed(() => {
  const now = new Date().toISOString()
  return topicRows.value.map((item, index) => ({
    id: `topic_${index}_${item.topic}`,
    from: 'Report',
    to: 'Dashboard',
    type: String(item.topic || 'data:report'),
    summary: `报告统计：${item.topic} × ${item.count}`,
    timestamp: now,
    data: { count: item.count, source: 'report-summary' },
  }))
})

const effectiveMessages = computed(() => {
  const live = Array.isArray(testStore.messages) ? testStore.messages : []
  return live.length > 0 ? live : fallbackMessagesFromReport.value
})

const filteredMessages = computed(() => effectiveMessages.value.filter((msg) => {
  if (logTypeFilters.value.length === 0) return true
  if (!msg?.type) return false
  return logTypeFilters.value.some((type) => msg.type.startsWith(type))
}))
const displayMessages = computed(() => [...filteredMessages.value].reverse())

watch(() => displayMessages.value.length, async () => {
  await nextTick()
  if (!autoScrollEnabled.value || !logContainerRef.value) return
  logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight
})

function onLogScroll() {
  const el = logContainerRef.value
  if (!el) return
  autoScrollEnabled.value = el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

function typeTagType(type = '') {
  if (type.startsWith('cmd')) return 'primary'
  if (type.startsWith('data')) return 'success'
  if (type.startsWith('feedback')) return 'warning'
  return 'info'
}

function logItemClass(type = '') {
  if (type.startsWith('cmd')) return 'log-cmd'
  if (type.startsWith('data')) return 'log-data'
  if (type.startsWith('feedback')) return 'log-feedback'
  return ''
}

function formatTimestamp(value) {
  if (!value) return '--'
  const date = new Date(value)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}
</script>

<template>
  <section class="dashboard-section log-section">
    <div class="section-heading">
      <div>
        <p class="section-kicker">Messages</p>
        <h2>智能体日志</h2>
      </div>
      <div class="log-summary-row">
        <span>发送方 {{ senderRows.length }} 项</span>
        <span>主题 {{ topicRows.length }} 项</span>
        <span>展示 {{ displayMessages.length }} 条</span>
      </div>
    </div>

    <div class="log-toolbar">
      <el-checkbox-group v-model="logTypeFilters" size="small">
        <el-checkbox-button value="cmd">命令</el-checkbox-button>
        <el-checkbox-button value="data">数据</el-checkbox-button>
        <el-checkbox-button value="feedback">反馈</el-checkbox-button>
      </el-checkbox-group>
      <el-button v-if="!autoScrollEnabled" text type="primary" @click="autoScrollEnabled = true">恢复自动滚动</el-button>
    </div>

    <div v-if="displayMessages.length === 0" class="empty-state-card">暂无实时日志，报告生成后会回放主题统计。</div>
    <transition-group v-else name="log-slide" tag="div" class="log-list" ref="logContainerRef" @scroll="onLogScroll">
      <div v-for="msg in displayMessages" :key="msg.id" class="log-item" :class="logItemClass(msg.type)">
        <span class="log-time">{{ formatTimestamp(msg.timestamp) }}</span>
        <span class="log-route">{{ msg.from }} → {{ msg.to }}</span>
        <el-tag size="small" :type="typeTagType(msg.type)">{{ msg.type }}</el-tag>
        <span class="log-summary">{{ msg.summary }}</span>
      </div>
    </transition-group>
  </section>
</template>

<style scoped>
.log-section { display: flex; flex-direction: column; gap: 10px; }
.section-heading, .log-summary-row, .log-toolbar { display: flex; align-items: center; }
.section-heading { justify-content: space-between; gap: 12px; }
.section-kicker { margin: 0 0 4px; color: #78a6d8; font-size: 12px; text-transform: uppercase; }
h2 { margin: 0; color: #eef7ff; font-size: 22px; }
.log-summary-row { gap: 16px; color: #8fa7d3; font-size: 12px; }
.log-toolbar { gap: 8px; }
.empty-state-card { min-height: 160px; border: 1px dashed rgba(0,212,255,.28); border-radius: 10px; background: rgba(9,16,30,.64); color: #9ab2db; display: flex; align-items: center; justify-content: center; }
.log-list { height: 500px; overflow: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px; border: 1px solid rgba(0,212,255,.16); border-radius: 10px; background: rgba(10,18,33,.72); }
.log-item { display: grid; grid-template-columns: 64px 160px auto 1fr; align-items: center; gap: 8px; border-left: 3px solid transparent; padding: 6px 8px; border-radius: 8px; background: rgba(7,13,24,.68); }
.log-cmd { border-left-color: #00d4ff; }
.log-data { border-left-color: #00ff88; }
.log-feedback { border-left-color: #ffaa00; }
.log-time { color: #7f95bd; font-size: 11px; }
.log-route { color: #bfd4fb; font-size: 12px; }
.log-summary { color: #d9e9ff; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.log-slide-enter-active, .log-slide-leave-active { transition: all .28s ease; }
.log-slide-enter-from { opacity: 0; transform: translateX(18px); }
.log-slide-leave-to { opacity: 0; transform: translateX(-12px); }
@media (max-width: 900px) { .section-heading { flex-direction: column; align-items: stretch; } .log-item { grid-template-columns: 54px 1fr; } .log-summary { grid-column: 1 / -1; } }
</style>
