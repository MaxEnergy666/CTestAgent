import { reactive } from 'vue'
import { testStore } from '../stores/testStore'

const AGENT_NAMES = [
  'Coordinator',
  'Analyzer',
  'Generator',
  'Executor',
  'Reviewer',
  'Reporter',
]

const MESSAGE_TYPES = [
  'cmd:start_analysis',
  'data:analysis_result',
  'cmd:generate',
  'data:test_cases',
  'data:approved_cases',
  'data:execution_results',
  'data:coverage_update',
  'feedback:review',
  'data:verified_crashes',
  'feedback:strategy',
]

const SUMMARY_POOL = [
  '启动新一轮测试调度',
  '发送测试用例批次',
  '执行结果回传',
  '覆盖率增量更新',
  '评审反馈策略建议',
  '缺陷验证完成',
  '进入下一阶段决策',
]

// 全局模拟运行态（供 HeaderBar 等组件共享）
export const simulationState = reactive({
  elapsedSeconds: 0,
})

let simulationTimer = null
let startTimestamp = null

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

function randomFloat(min, max) {
  return Math.random() * (max - min) + min
}

function randomPick(list) {
  return list[randomInt(0, list.length - 1)]
}

function buildMessage(round) {
  const from = randomPick(AGENT_NAMES)
  const toCandidates = AGENT_NAMES.filter((name) => name !== from)
  const to = randomPick(toCandidates)
  const type = randomPick(MESSAGE_TYPES)

  return {
    id: `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    from,
    to,
    type,
    summary: randomPick(SUMMARY_POOL),
    timestamp: new Date().toISOString(),
    data: {
      round,
      seed: Math.random().toString(36).slice(2, 10),
    },
  }
}

function updatePhase() {
  const roundRatio = testStore.currentRound / testStore.maxRounds
  const coverage = testStore.coverage.current

  if (testStore.status === 'finished' || coverage >= 98 || roundRatio >= 0.95) {
    testStore.phase = 'FINALIZE'
    return
  }

  if (coverage >= 60 || roundRatio >= 0.5) {
    testStore.phase = 'DEEP'
    return
  }

  if (testStore.currentRound > 0) {
    testStore.phase = 'EXPLORE'
  } else {
    testStore.phase = 'INIT'
  }
}

function updateAgentStatusByMessages(roundMessages) {
  // 先重置为等待态
  AGENT_NAMES.forEach((name) => {
    testStore.agents[name].status = 'waiting'
  })

  // Coordinator 每轮都参与调度
  testStore.agents.Coordinator.status = 'working'
  testStore.agents.Coordinator.lastAction = `调度第 ${testStore.currentRound} 轮`

  roundMessages.forEach((msg) => {
    if (testStore.agents[msg.from]) {
      testStore.agents[msg.from].status = 'working'
      testStore.agents[msg.from].messageCount += 1
      testStore.agents[msg.from].lastAction = `发送 ${msg.type}`
    }

    if (testStore.agents[msg.to]) {
      testStore.agents[msg.to].status = 'waiting'
      testStore.agents[msg.to].lastAction = `接收 ${msg.type}`
    }
  })

  // FINALIZE 阶段让 Reporter 进入工作态
  if (testStore.phase === 'FINALIZE') {
    testStore.agents.Reporter.status = 'working'
    testStore.agents.Reporter.lastAction = '生成最终报告'
  }
}

/**
 * 模拟执行一轮测试
 * - 随机生成 3~5 条消息
 * - 覆盖率增长 0.5~2%
 * - 10% 概率发现新缺陷
 * - 更新各智能体状态
 */
export function simulateRound() {
  if (testStore.status === 'paused' || testStore.status === 'finished') {
    return null
  }

  if (testStore.status === 'idle') {
    testStore.status = 'running'
    testStore.phase = 'INIT'
  }

  if (testStore.currentRound >= testStore.maxRounds) {
    testStore.status = 'finished'
    testStore.phase = 'FINALIZE'
    return null
  }

  testStore.currentRound += 1

  // 1) 生成消息流
  const messageCount = randomInt(3, 5)
  const roundMessages = Array.from({ length: messageCount }, () =>
    buildMessage(testStore.currentRound),
  )
  testStore.messages.unshift(...roundMessages)

  // 控制消息列表上限，避免演示时无限增长
  if (testStore.messages.length > 300) {
    testStore.messages.length = 300
  }

  // 2) 覆盖率增长（0.5~2.0）
  const delta = randomFloat(0.5, 2)
  const nextCoverage = Math.min(testStore.coverage.target, testStore.coverage.current + delta)
  testStore.coverage.current = Number(nextCoverage.toFixed(2))
  testStore.coverage.history.push({
    round: testStore.currentRound,
    value: testStore.coverage.current,
  })

  // 3) 统计指标（演示数据）
  const totalCases = randomInt(30, 80)
  const approved = randomInt(Math.floor(totalCases * 0.6), Math.floor(totalCases * 0.95))
  const rejected = totalCases - approved

  testStore.metrics.totalCases += totalCases
  testStore.metrics.approved += approved
  testStore.metrics.rejected += rejected

  // 4) 10% 概率发现新缺陷
  let newCrash = null
  if (Math.random() < 0.1) {
    newCrash = {
      id: `crash_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      type: randomPick(['heap-buffer-overflow', 'segmentation-fault', 'use-after-free']),
      input: `seed_${Math.random().toString(36).slice(2, 9)}`,
      stackTrace: 'mock_stack_trace: main -> executor -> target_function',
      verified: Math.random() > 0.3,
      severity: randomPick(['low', 'medium', 'high', 'critical']),
    }
    testStore.crashes.unshift(newCrash)
    testStore.metrics.uniqueCrashes += 1
  }

  // 5) 小概率误报拦截
  if (Math.random() < 0.15) {
    testStore.metrics.falsePositives += 1
  }

  // 更新阶段与智能体状态
  updatePhase()
  updateAgentStatusByMessages(roundMessages)

  // 结束判定
  if (
    testStore.currentRound >= testStore.maxRounds ||
    testStore.coverage.current >= testStore.coverage.target
  ) {
    testStore.status = 'finished'
    testStore.phase = 'FINALIZE'
  }

  return {
    round: testStore.currentRound,
    newMessages: roundMessages,
    coverage: testStore.coverage.current,
    newCrash,
  }
}

/** 重置模拟测试状态 */
export function resetSimulationState() {
  testStore.status = 'idle'
  testStore.phase = 'INIT'
  testStore.currentRound = 0
  testStore.coverage.current = 0
  testStore.coverage.history = [{ round: 0, value: 0 }]
  testStore.messages = []
  testStore.crashes = []
  testStore.metrics.totalCases = 0
  testStore.metrics.approved = 0
  testStore.metrics.rejected = 0
  testStore.metrics.falsePositives = 0
  testStore.metrics.uniqueCrashes = 0

  Object.values(testStore.agents).forEach((agent) => {
    agent.status = 'idle'
    agent.messageCount = 0
    agent.lastAction = '等待启动'
  })

  simulationState.elapsedSeconds = 0
  startTimestamp = null
}

/** 启动模拟测试（共享单例定时器） */
export function startSimulation() {
  // 仅演示模式允许本地模拟
  if (testStore.runMode !== 'demo') return

  if (testStore.status === 'running') return

  if (testStore.status === 'finished') {
    resetSimulationState()
  }

  testStore.status = 'running'
  if (!startTimestamp) {
    startTimestamp = Date.now() - simulationState.elapsedSeconds * 1000
  }

  // 先推进一轮，保证点击后立即看到变化
  simulateRound()

  if (simulationTimer) return

  simulationTimer = window.setInterval(() => {
    if (testStore.status !== 'running') {
      return
    }

    simulationState.elapsedSeconds = Math.max(
      0,
      Math.floor((Date.now() - Number(startTimestamp)) / 1000),
    )

    const result = simulateRound()
    if (!result && testStore.status !== 'running') {
      stopSimulationInternal()
    }
  }, 2000)
}

/** 暂停模拟测试 */
export function pauseSimulation() {
  if (testStore.status !== 'running') return
  testStore.status = 'paused'
  stopSimulationInternal(false)
}

/** 停止模拟测试并重置 */
export function stopSimulation(resetState = true) {
  stopSimulationInternal(false)
  if (resetState) {
    resetSimulationState()
  }
}

function stopSimulationInternal(resetTimerState = true) {
  if (simulationTimer) {
    window.clearInterval(simulationTimer)
    simulationTimer = null
  }

  if (resetTimerState) {
    simulationState.elapsedSeconds = 0
    startTimestamp = null
  }
}
