<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { testStore } from '../stores/testStore'
import AnimatedNumber from './AnimatedNumber.vue'

const nodeList = [
  { key: 'Coordinator', icon: '🧭', x: 50, y: 10 },
  { key: 'Analyzer', icon: '🔍', x: 23, y: 28 },
  { key: 'Generator', icon: '🧪', x: 23, y: 72 },
  { key: 'Executor', icon: '⚙️', x: 77, y: 28 },
  { key: 'Reviewer', icon: '🛡️', x: 77, y: 72 },
  { key: 'Reporter', icon: '📊', x: 50, y: 90 },
]

const nodeMap = Object.fromEntries(nodeList.map((item) => [item.key, item]))

const links = []
for (let i = 0; i < nodeList.length; i += 1) {
  for (let j = i + 1; j < nodeList.length; j += 1) {
    const a = nodeList[i].key
    const b = nodeList[j].key
    links.push({
      from: a,
      to: b,
      key: buildLinkKey(a, b),
    })
  }
}

const activeFlows = ref([])
const seenMessageIds = new Set()
const flowTimeoutMap = new Map()

const lastTotalCases = ref(0)
const currentRoundCases = ref(0)

const coverageValue = computed(() => Number(testStore.coverage.current || 0))
const uniqueCrashCount = computed(() => testStore.metrics.uniqueCrashes || 0)
const coverageStyle = computed(() => ({
  background: `conic-gradient(#00d4ff ${coverageValue.value}%, rgba(0, 212, 255, 0.12) ${coverageValue.value}% 100%)`,
}))

const activeLinkSet = computed(() => new Set(activeFlows.value.map((item) => item.linkKey)))

function buildLinkKey(a, b) {
  return [a, b].sort().join('__')
}

function getFlowClassByType(type = '') {
  if (type.startsWith('cmd')) return 'flow-cmd'
  if (type.startsWith('data')) return 'flow-data'
  if (type.startsWith('feedback')) return 'flow-feedback'
  return 'flow-cmd'
}

function isRejectMessage(msg = {}) {
  if (msg?.from !== 'Reviewer') return false
  const summary = String(msg?.summary || '')
  return (
    msg?.type?.startsWith('feedback') &&
    /reject|拒绝|low_score|redundant|驳回/i.test(summary)
  )
}

function getStatusText(status = 'idle') {
  if (status === 'working') return '运行中'
  if (status === 'waiting') return '等待中'
  return '空闲'
}

function getNodeStyle(agentKey) {
  const node = nodeMap[agentKey]
  return {
    left: `${node.x}%`,
    top: `${node.y}%`,
  }
}

function getNodeStatusClass(agentKey) {
  const status = testStore.agents?.[agentKey]?.status || 'idle'
  if (status === 'working') return 'status-working'
  if (status === 'waiting') return 'status-waiting'
  return 'status-idle'
}

function linePath(link) {
  const from = nodeMap[link.from]
  const to = nodeMap[link.to]
  return `M ${from.x} ${from.y} L ${to.x} ${to.y}`
}

function isLinkActive(linkKey) {
  return activeLinkSet.value.has(linkKey)
}

function getFlowStyle(flow) {
  return {
    '--start-x': `${flow.fromNode.x}%`,
    '--start-y': `${flow.fromNode.y}%`,
    '--dx': `${flow.toNode.x - flow.fromNode.x}%`,
    '--dy': `${flow.toNode.y - flow.fromNode.y}%`,
  }
}

function removeFlow(flowId) {
  activeFlows.value = activeFlows.value.filter((item) => item.id !== flowId)
  const timeoutRef = flowTimeoutMap.get(flowId)
  if (timeoutRef) {
    window.clearTimeout(timeoutRef)
    flowTimeoutMap.delete(flowId)
  }
}

function addFlowFromMessage(msg) {
  if (!msg?.id || seenMessageIds.has(msg.id)) return
  if (!msg.from || !msg.to) return
  if (!nodeMap[msg.from] || !nodeMap[msg.to]) return

  seenMessageIds.add(msg.id)

  const flow = {
    id: msg.id,
    from: msg.from,
    to: msg.to,
    fromNode: nodeMap[msg.from],
    toNode: nodeMap[msg.to],
    className: isRejectMessage(msg) ? 'flow-reject' : getFlowClassByType(msg.type),
    linkKey: buildLinkKey(msg.from, msg.to),
  }

  activeFlows.value.push(flow)

  const timeoutRef = window.setTimeout(() => {
    removeFlow(flow.id)
  }, 1500)
  flowTimeoutMap.set(flow.id, timeoutRef)

  // 控制同屏动画数量
  if (activeFlows.value.length > 18) {
    const overflow = activeFlows.value.splice(0, activeFlows.value.length - 18)
    overflow.forEach((item) => removeFlow(item.id))
  }
}

watch(
  () => testStore.messages.length,
  () => {
    const latestBatch = testStore.messages.slice(0, 10)
    latestBatch.forEach((msg) => addFlowFromMessage(msg))
  },
)

watch(
  () => testStore.metrics.totalCases,
  (newVal) => {
    const delta = newVal - lastTotalCases.value
    currentRoundCases.value = delta > 0 ? delta : 0
    lastTotalCases.value = newVal
  },
)

watch(
  () => testStore.status,
  (status) => {
    if (status === 'idle') {
      activeFlows.value = []
      seenMessageIds.clear()
      currentRoundCases.value = 0
      lastTotalCases.value = 0
    }
  },
)

onMounted(() => {})

onBeforeUnmount(() => {
  flowTimeoutMap.forEach((timeoutRef) => window.clearTimeout(timeoutRef))
  flowTimeoutMap.clear()
})
</script>

<template>
  <div class="agent-flow-view">
    <svg class="link-layer" viewBox="0 0 100 100" preserveAspectRatio="none">
      <path
        v-for="link in links"
        :key="link.key"
        :d="linePath(link)"
        class="link-line"
        :class="{ active: isLinkActive(link.key) }"
      />
    </svg>

    <transition-group name="flow-fade" tag="div" class="flow-layer">
      <div
        v-for="flow in activeFlows"
        :key="flow.id"
        class="flow-dot"
        :class="flow.className"
        :style="getFlowStyle(flow)"
        @animationend="removeFlow(flow.id)"
      ></div>
    </transition-group>

    <div
      v-for="node in nodeList"
      :key="node.key"
      class="agent-node"
      :class="getNodeStatusClass(node.key)"
      :style="getNodeStyle(node.key)"
    >
      <div class="node-icon">{{ node.icon }}</div>
      <div class="node-name">{{ node.key }}</div>
      <div class="node-status">{{ getStatusText(testStore.agents?.[node.key]?.status) }}</div>
    </div>

    <div class="center-stats">
      <div class="coverage-ring" :style="coverageStyle">
        <div class="ring-inner">
          <div class="coverage-value"><AnimatedNumber :value="coverageValue" :decimals="2" suffix="%" /></div>
          <div class="coverage-label">Coverage</div>
        </div>
      </div>

      <div class="stats-row">
        <div class="stat-card">
          <span class="stat-label">已发现缺陷</span>
          <span class="stat-value danger"><AnimatedNumber :value="Number(uniqueCrashCount)" /></span>
        </div>
        <div class="stat-card">
          <span class="stat-label">本轮用例数</span>
          <span class="stat-value"><AnimatedNumber :value="currentRoundCases" /></span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-flow-view {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  border-radius: 12px;
  background:
    radial-gradient(circle at 50% 50%, rgba(0, 212, 255, 0.08), rgba(0, 0, 0, 0) 48%),
    linear-gradient(160deg, rgba(6, 12, 24, 0.9), rgba(8, 17, 34, 0.92));
  overflow: hidden;
}

.link-layer,
.flow-layer {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.link-line {
  fill: none;
  stroke: rgba(146, 174, 216, 0.24);
  stroke-width: 0.35;
  transition: stroke 0.25s ease, stroke-width 0.25s ease;
}

.link-line.active {
  stroke: rgba(0, 212, 255, 0.95);
  stroke-width: 0.55;
  filter: drop-shadow(0 0 6px rgba(0, 212, 255, 0.6));
}

.agent-node {
  position: absolute;
  width: 120px;
  height: 80px;
  transform: translate(-50%, -50%);
  border-radius: 14px;
  border: 1px solid rgba(100, 119, 150, 0.35);
  background: rgba(15, 22, 38, 0.82);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  z-index: 3;
}

.node-icon {
  font-size: 18px;
}

.node-name {
  font-size: 13px;
  color: #eaf3ff;
  font-weight: 600;
}

.node-status {
  font-size: 11px;
  color: #9fb2d3;
}

.status-idle {
  border-color: rgba(108, 124, 151, 0.35);
}

.status-working {
  border-color: rgba(0, 212, 255, 0.9);
  box-shadow: 0 0 14px rgba(0, 212, 255, 0.55), inset 0 0 12px rgba(0, 212, 255, 0.16);
  animation: breathe 1.8s ease-in-out infinite;
}

.status-waiting {
  border-color: rgba(112, 176, 255, 0.55);
  animation: waitingBlink 1.6s ease-in-out infinite;
}

@keyframes breathe {
  0%,
  100% {
    box-shadow: 0 0 10px rgba(0, 212, 255, 0.38), inset 0 0 8px rgba(0, 212, 255, 0.1);
  }
  50% {
    box-shadow: 0 0 22px rgba(0, 212, 255, 0.8), inset 0 0 16px rgba(0, 212, 255, 0.24);
  }
}

@keyframes waitingBlink {
  0%,
  100% {
    border-color: rgba(112, 176, 255, 0.35);
  }
  50% {
    border-color: rgba(112, 176, 255, 0.75);
  }
}

.flow-dot {
  position: absolute;
  left: var(--start-x);
  top: var(--start-y);
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  animation: flyAlong 1.4s linear forwards;
  z-index: 4;
  overflow: visible;
}

.flow-dot::after {
  content: '';
  position: absolute;
  left: -7px;
  top: 2px;
  width: 16px;
  height: 6px;
  border-radius: 999px;
  background: inherit;
  filter: blur(3px);
  opacity: 0.55;
  transform: rotate(-20deg);
}

.flow-cmd {
  background: #00d4ff;
  box-shadow: 0 0 12px #00d4ff;
}

.flow-data {
  background: #00ff88;
  box-shadow: 0 0 12px #00ff88;
}

.flow-feedback {
  background: #ffaa00;
  box-shadow: 0 0 12px #ffaa00;
}

.flow-reject {
  background: #ff4d4f;
  box-shadow: 0 0 16px rgba(255, 77, 79, 0.95), 0 0 26px rgba(255, 77, 79, 0.55);
  animation: flyAlong 1.1s linear forwards, rejectBurst 1.1s ease-out forwards;
}

.flow-reject::before {
  content: '';
  position: absolute;
  inset: -8px;
  border-radius: 50%;
  border: 1px dashed rgba(255, 122, 122, 0.8);
  animation: rejectShard 0.45s ease-out 0.75s forwards;
  opacity: 0;
}

@keyframes rejectBurst {
  0% {
    box-shadow: 0 0 10px rgba(255, 77, 79, 0.8), 0 0 18px rgba(255, 77, 79, 0.4);
  }
  70% {
    box-shadow: 0 0 18px rgba(255, 77, 79, 1), 0 0 30px rgba(255, 77, 79, 0.75);
  }
  100% {
    box-shadow: 0 0 8px rgba(255, 77, 79, 0.35);
  }
}

@keyframes rejectShard {
  0% {
    opacity: 0;
    transform: scale(0.6);
  }
  35% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: scale(1.6);
  }
}

@keyframes flyAlong {
  0% {
    transform: translate(-50%, -50%) scale(0.8);
    opacity: 0;
  }
  15% {
    opacity: 1;
  }
  100% {
    transform: translate(calc(-50% + var(--dx)), calc(-50% + var(--dy))) scale(1.05);
    opacity: 0;
  }
}

.flow-fade-enter-active,
.flow-fade-leave-active {
  transition: opacity 0.2s ease;
}

.flow-fade-enter-from,
.flow-fade-leave-to {
  opacity: 0;
}

.center-stats {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  z-index: 2;
}

.coverage-ring {
  width: 150px;
  height: 150px;
  border-radius: 50%;
  padding: 10px;
  box-shadow: 0 0 24px rgba(0, 212, 255, 0.22);
}

.ring-inner {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  background: rgba(8, 15, 30, 0.95);
  border: 1px solid rgba(0, 212, 255, 0.22);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.coverage-value {
  font-size: 28px;
  font-weight: 700;
  color: #dff9ff;
  text-shadow: 0 0 12px rgba(0, 212, 255, 0.35);
}

.coverage-label {
  font-size: 12px;
  color: #8eb2d9;
  letter-spacing: 0.5px;
}

.stats-row {
  display: flex;
  gap: 10px;
}

.stat-card {
  min-width: 132px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(0, 212, 255, 0.22);
  background: rgba(10, 18, 33, 0.82);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-label {
  font-size: 12px;
  color: #8da2c6;
}

.stat-value {
  font-size: 20px;
  color: #e9f7ff;
  font-weight: 700;
}

.stat-value.danger {
  color: #ff8e8e;
}
</style>
