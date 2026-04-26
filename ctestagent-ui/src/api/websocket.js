import { testStore } from '../stores/testStore'
import { API_BASE_URL } from './http'
import { applyElapsedSecondsFromServer } from './elapsedSync'

function resolveWSUrl() {
  const envWs = String(import.meta.env.VITE_WS_URL || '').trim()
  if (envWs) return envWs

  // 复用 API 基址推导 WS 地址，保证同一后端实例。
  const apiBase = String(API_BASE_URL || '').replace(/\/$/, '')
  if (apiBase) {
    const origin = apiBase.endsWith('/api') ? apiBase.slice(0, -4) : apiBase
    if (origin.startsWith('https://')) return `${origin.replace('https://', 'wss://')}/ws/events`
    if (origin.startsWith('http://')) return `${origin.replace('http://', 'ws://')}/ws/events`
  }

  return 'ws://localhost:8000/ws/events'
}

const WS_URL = resolveWSUrl()
const RECONNECT_DELAY = 3000
const MAX_RECONNECT_ATTEMPTS = 5
const COVERAGE_HISTORY_MIN_INTERVAL_MS = 5000

function getIncomingRunId(payload = {}) {
  const runId = Number(payload?.runId || 0)
  return Number.isFinite(runId) && runId > 0 ? runId : 0
}

function isRunCompatible(payload = {}) {
  const incomingRunId = getIncomingRunId(payload)
  const activeRunId = Number(testStore.activeRunId || 0)

  if (incomingRunId > 0 && activeRunId > 0 && incomingRunId !== activeRunId) {
    return false
  }

  if (incomingRunId > 0 && activeRunId === 0) {
    testStore.activeRunId = incomingRunId
  }

  return true
}

function pushMessageEvent(payload = {}) {
  const message = {
    id: payload.id || `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    from: payload.from || 'System',
    to: payload.to || 'Broadcast',
    type: payload.type || 'data:unknown',
    summary: payload.summary || '收到实时事件',
    timestamp: payload.timestamp || new Date().toISOString(),
    data: payload.data || {},
  }

  testStore.messages.unshift(message)
  if (testStore.messages.length > 300) {
    testStore.messages.length = 300
  }

  if (testStore.agents[message.from]) {
    testStore.agents[message.from].status = 'working'
    testStore.agents[message.from].messageCount += 1
    testStore.agents[message.from].lastAction = `发送 ${message.type}`
  }

  if (testStore.agents[message.to]) {
    testStore.agents[message.to].status = 'waiting'
    testStore.agents[message.to].lastAction = `接收 ${message.type}`
  }
}

function appendCoverageHistory(round, value, updatedAtMs = 0) {
  const safeRound = Math.max(0, Number(round || 0))
  const safeValue = Math.max(0, Math.min(100, Number(value || 0)))
  const safeTs = Number(updatedAtMs || 0) > 0 ? Number(updatedAtMs) : Date.now()

  const history = testStore.coverage.history || []
  if (history.length === 0) {
    history.push({ round: safeRound, value: safeValue, ts: safeTs })
    return
  }

  const last = history[history.length - 1]
  if (!last) {
    history.push({ round: safeRound, value: safeValue, ts: safeTs })
    return
  }

  const lastRound = Number(last.round || 0)
  const lastTs = Number(last.ts || 0)
  if (safeRound > 0 && safeRound !== lastRound) {
    history.push({ round: safeRound, value: safeValue, ts: safeTs })
    return
  }

  if (safeTs - lastTs >= COVERAGE_HISTORY_MIN_INTERVAL_MS) {
    history.push({ round: lastRound, value: safeValue, ts: safeTs })
    return
  }

  last.value = safeValue
  last.ts = safeTs
}

function applyCoveragePayload(payload = {}, fallbackRound = 0) {
  const coverageObj = payload.coverage && typeof payload.coverage === 'object' ? payload.coverage : {}

  const lineCoverage = Number(payload.lineCoverage ?? coverageObj.line ?? payload.current ?? payload.coverage ?? testStore.coverage.current)
  const branchCoverage = Number(payload.branchCoverage ?? coverageObj.branch ?? testStore.coverage.branch)
  const lineCovered = Number(payload.lineCovered ?? coverageObj.lineCovered ?? testStore.coverage.lineCovered)
  const lineTotal = Number(payload.lineTotal ?? coverageObj.lineTotal ?? payload.totalSourceLines ?? testStore.coverage.lineTotal)
  const branchCovered = Number(payload.branchCovered ?? coverageObj.branchCovered ?? testStore.coverage.branchCovered)
  const branchTotal = Number(payload.branchTotal ?? coverageObj.branchTotal ?? testStore.coverage.branchTotal)
  const updatedAt = Number(payload.coverageUpdatedAt ?? coverageObj.updatedAt ?? payload.updatedAt ?? 0)

  if (Number.isFinite(lineCoverage)) {
    testStore.coverage.current = Math.max(0, Math.min(100, Number(lineCoverage.toFixed(2))))
  }
  if (Number.isFinite(branchCoverage)) {
    testStore.coverage.branch = Math.max(0, Math.min(100, Number(branchCoverage.toFixed(2))))
  }
  if (Number.isFinite(lineCovered) && lineCovered >= 0) {
    testStore.coverage.lineCovered = Math.round(lineCovered)
  }
  if (Number.isFinite(lineTotal) && lineTotal >= 0) {
    testStore.coverage.lineTotal = Math.round(lineTotal)
    testStore.totalSourceLines = Math.round(lineTotal)
  }
  if (Number.isFinite(branchCovered) && branchCovered >= 0) {
    testStore.coverage.branchCovered = Math.round(branchCovered)
  }
  if (Number.isFinite(branchTotal) && branchTotal >= 0) {
    testStore.coverage.branchTotal = Math.round(branchTotal)
  }
  if (Number.isFinite(updatedAt) && updatedAt > 0) {
    testStore.coverage.lastUpdatedAt = Math.round(updatedAt)
  }

  const roundRaw = payload.round ?? payload.currentRound ?? fallbackRound ?? testStore.currentRound ?? 0
  const round = Math.max(0, Number(roundRaw))
  appendCoverageHistory(round, testStore.coverage.current, updatedAt)
}

function applyStatusSnapshot(payload = {}) {
  if (!payload || typeof payload !== 'object') return
  const incomingRunId = Number(payload.runId || 0)
  const currentRunId = Number(testStore.activeRunId || 0)
  const hasRunningHint =
    String(payload.status || '').toLowerCase() === 'running' ||
    Number(payload.currentRound || 0) > 0
  if (incomingRunId <= 0 && currentRunId > 0 && hasRunningHint) return
  if (!isRunCompatible(payload)) return

  if (payload.status) testStore.status = payload.status
  if (payload.stopReason !== undefined) {
    testStore.stopReason = String(payload.stopReason || '')
  }
  if (payload.lastError !== undefined) {
    testStore.lastError = String(payload.lastError || '')
  }
  if (payload.phase) testStore.phase = payload.phase
  if (payload.currentRound !== undefined) {
    testStore.currentRound = Number(payload.currentRound)
  }
  if (payload.maxRounds !== undefined) {
    const maxRounds = Number(payload.maxRounds)
    if (Number.isFinite(maxRounds) && maxRounds > 0) {
      testStore.maxRounds = maxRounds
      testStore.config.maxRounds = maxRounds
      testStore.config.maxRoundsHardCap = Math.max(Number(testStore.config.maxRoundsHardCap || 0), maxRounds)
    }
  }
  if (payload.maxTimeSeconds !== undefined) {
    const maxTimeSeconds = Number(payload.maxTimeSeconds)
    if (Number.isFinite(maxTimeSeconds) && maxTimeSeconds > 0) {
      testStore.config.maxTime = Math.ceil(maxTimeSeconds / 60)
    }
  }
  if (payload.coverageGoalLine !== undefined) {
    const goalLine = Number(payload.coverageGoalLine)
    if (Number.isFinite(goalLine) && goalLine >= 0) {
      testStore.config.coverageGoalLine = goalLine
    }
  }
  if (payload.coverageGoalBranch !== undefined) {
    const goalBranch = Number(payload.coverageGoalBranch)
    if (Number.isFinite(goalBranch) && goalBranch >= 0) {
      testStore.config.coverageGoalBranch = goalBranch
    }
  }
  if (payload.totalSourceLines !== undefined) {
    const totalSourceLines = Number(payload.totalSourceLines)
    if (Number.isFinite(totalSourceLines) && totalSourceLines >= 0) {
      testStore.totalSourceLines = totalSourceLines
      testStore.coverage.lineTotal = Math.round(totalSourceLines)
    }
  }

  applyCoveragePayload(payload, payload.currentRound)

  if (payload.elapsedSeconds !== undefined) {
    applyElapsedSecondsFromServer(payload.elapsedSeconds, {
      runId: incomingRunId,
      status: payload.status,
    })
  }

  if (payload.metrics && typeof payload.metrics === 'object') {
    Object.keys(testStore.metrics).forEach((key) => {
      if (payload.metrics[key] !== undefined) {
        testStore.metrics[key] = Number(payload.metrics[key])
      }
    })
  }
}

function resetRealtimeConnectionState() {
  testStore.realtime.connected = false
  testStore.realtime.reconnectAttempts = 0
  testStore.realtime.maxReconnectAttempts = MAX_RECONNECT_ATTEMPTS
  testStore.realtime.lastError = ''
}

class EventWebSocketClient {
  constructor() {
    this.ws = null
    this.reconnectTimer = null
    this.manualClose = false
  }

  connect() {
    // 避免演示模式误连接
    if (testStore.runMode !== 'realtime') return

    if (this.ws && this.ws.readyState === WebSocket.OPEN) return

    this.manualClose = false

    try {
      this.ws = new WebSocket(WS_URL)
      this._bindEvents()
    } catch (error) {
      this._handleError(error)
      this._scheduleReconnect()
    }
  }

  disconnect(manual = true) {
    this.manualClose = manual

    if (this.reconnectTimer) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }

    if (this.ws) {
      try {
        this.ws.close()
      } catch (error) {
        console.error('WebSocket 关闭失败:', error)
      }
      this.ws = null
    }

    testStore.realtime.connected = false
  }

  _bindEvents() {
    if (!this.ws) return

    this.ws.onopen = () => {
      testStore.realtime.connected = true
      testStore.realtime.reconnectAttempts = 0
      testStore.realtime.lastError = ''
    }

    this.ws.onmessage = (event) => {
      this._handleMessage(event.data)
    }

    this.ws.onerror = (error) => {
      this._handleError(error)
    }

    this.ws.onclose = () => {
      testStore.realtime.connected = false
      if (!this.manualClose && testStore.runMode === 'realtime') {
        this._scheduleReconnect()
      }
    }
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return

    const currentAttempts = Number(testStore.realtime.reconnectAttempts || 0)
    if (currentAttempts >= MAX_RECONNECT_ATTEMPTS) {
      testStore.realtime.lastError = `重连失败：已超过最大重试次数(${MAX_RECONNECT_ATTEMPTS})`
      return
    }

    testStore.realtime.reconnectAttempts = currentAttempts + 1
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, RECONNECT_DELAY)
  }

  _handleError(error) {
    const msg = error?.message || 'WebSocket 连接异常'
    testStore.realtime.lastError = msg
    console.error('WebSocket Error:', error)
  }

  _handleMessage(raw) {
    let parsed = null
    try {
      parsed = JSON.parse(raw)
    } catch (error) {
      console.error('WebSocket 消息解析失败:', raw)
      return
    }

    const eventType = parsed?.event || parsed?.type
    const payload = parsed?.data ?? parsed?.payload ?? {}

    switch (eventType) {
      case 'status': {
        applyStatusSnapshot(payload)
        break
      }

      case 'agent_status': {
        const agentName = payload.agent || payload.name
        if (!agentName || !testStore.agents[agentName]) return
        if (payload.status) {
          testStore.agents[agentName].status = payload.status
        }
        if (payload.messageCount !== undefined) {
          testStore.agents[agentName].messageCount = payload.messageCount
        }
        if (payload.lastAction) {
          testStore.agents[agentName].lastAction = payload.lastAction
        }
        break
      }

      case 'message_event': {
        if (!isRunCompatible(payload)) return
        pushMessageEvent(payload)

        // Reporter 已明确产出报告时，立即拉取一次，避免 finished 窗口期底部面板空白。
        if (payload?.type === 'data:report_ready') {
          import('./runtimeControl')
            .then(({ fetchLatestReport }) => fetchLatestReport(false))
            .catch(() => {})
        }

        break
      }

      case 'coverage_update': {
        if (!isRunCompatible(payload)) return
        applyCoveragePayload(payload, payload.round)
        break
      }

      case 'crash_found': {
        if (!isRunCompatible(payload)) return
        const crash = {
          id: payload.id || `crash_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          type: payload.type || payload.errorType || 'unknown',
          input: payload.input || payload.minInput || '',
          stackTrace: payload.stackTrace || payload.stack || payload.description || `${payload.type || payload.errorType || 'unknown'} (runtime crash)`,
          description: payload.description || '',
          location: payload.location || '',
          risk: payload.risk || payload.description || '',
          occurrences: Math.max(1, Number(payload.occurrences || 1)),
          verified: payload.verified !== undefined ? Boolean(payload.verified) : true,
          severity: payload.riskLevel || payload.severity || 'medium',
        }
        testStore.crashes.unshift(crash)
        testStore.metrics.uniqueCrashes = Number(testStore.metrics.uniqueCrashes || 0) + 1
        break
      }

      case 'phase_change': {
        if (!isRunCompatible(payload)) return
        if (payload.phase) {
          testStore.phase = payload.phase
        }
        break
      }

      case 'round_complete': {
        if (!isRunCompatible(payload)) return
        if (payload.currentRound !== undefined) {
          testStore.currentRound = Number(payload.currentRound)
        } else if (payload.round !== undefined) {
          testStore.currentRound = Number(payload.round)
        }

        if (payload.metrics && typeof payload.metrics === 'object') {
          const nextMetrics = payload.metrics
          Object.keys(testStore.metrics).forEach((key) => {
            if (nextMetrics[key] !== undefined) {
              testStore.metrics[key] = Number(nextMetrics[key])
            }
          })
        }
        break
      }

      case 'runtime_error': {
        if (!isRunCompatible(payload)) return
        if (payload.message) {
          testStore.lastError = String(payload.message)
        }
        if (payload.fatal) {
          testStore.stopReason = 'executor_compile_failed'
        }
        pushMessageEvent({
          from: 'Server',
          to: 'Frontend',
          type: `data:${payload.type || 'runtime_error'}`,
          summary: payload.fatal ? '检测到致命运行错误' : '检测到运行错误',
          data: payload,
        })
        break
      }

      case 'test_finished': {
        if (!isRunCompatible(payload)) return
        testStore.status = 'finished'
        import('./runtimeControl')
          .then(({ fetchLatestReport }) => fetchLatestReport(false))
          .catch(() => {})
        break
      }

      default:
        // 未知事件：记录到消息流便于调试
        pushMessageEvent({
          from: 'Backend',
          to: 'Frontend',
          type: `data:${eventType || 'unknown'}`,
          summary: '收到未映射事件',
          data: payload,
        })
    }
  }
}

export const wsClient = new EventWebSocketClient()

export function startRealtimeMode() {
  testStore.runMode = 'realtime'
  resetRealtimeConnectionState()
  wsClient.connect()
}

export function stopRealtimeMode() {
  wsClient.disconnect(true)
  testStore.realtime.connected = false
}
