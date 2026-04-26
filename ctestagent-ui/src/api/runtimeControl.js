import { ElMessage } from 'element-plus'
import { testStore } from '../stores/testStore'
import { getReport, getStatus, startTest, stopTest, uploadFiles } from './http'
import { applyElapsedSecondsFromServer, resetElapsedSyncState } from './elapsedSync'
import { startRealtimeMode, stopRealtimeMode } from './websocket'

let statusTimer = null
let finishedReportRetryCount = 0
const MAX_FINISHED_REPORT_RETRY = 30
const COVERAGE_HISTORY_MIN_INTERVAL_MS = 5000

const TOPIC_ROUTE_MAP = {
  'cmd:start_analysis': { from: 'Coordinator', to: 'Analyzer' },
  'data:analysis_result': { from: 'Analyzer', to: 'Broadcast' },
  'cmd:generate': { from: 'Coordinator', to: 'Generator' },
  'data:test_cases': { from: 'Generator', to: 'Reviewer' },
  'data:approved_cases': { from: 'Reviewer', to: 'Executor' },
  'data:execution_results': { from: 'Executor', to: 'Broadcast' },
  'data:coverage_update': { from: 'Executor', to: 'Broadcast' },
  'feedback:review': { from: 'Reviewer', to: 'Generator' },
  'data:verified_crashes': { from: 'Reviewer', to: 'Broadcast' },
  'feedback:strategy': { from: 'Reviewer', to: 'Coordinator' },
  'cmd:phase_change': { from: 'Coordinator', to: 'Broadcast' },
  'cmd:finalize': { from: 'Coordinator', to: 'Reporter' },
  'data:report_ready': { from: 'Reporter', to: 'Coordinator' },
}

function buildFilesFingerprint(files = []) {
  return files
    .map((file) => {
      const name = String(file?.name || '')
      const size = Number(file?.size || 0)
      const lastModified = Number(file?.lastModified || 0)
      return `${name}:${size}:${lastModified}`
    })
    .sort()
    .join('|')
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

function applyStatusPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return

  const incomingRunId = Number(payload.runId || 0)
  const currentRunId = Number(testStore.activeRunId || 0)
  if (incomingRunId > 0 && currentRunId > 0 && incomingRunId !== currentRunId) {
    return
  }
  const hasRunningHint =
    String(payload.status || '').toLowerCase() === 'running' ||
    Number(payload.currentRound || 0) > 0
  if (incomingRunId <= 0 && currentRunId > 0 && hasRunningHint) {
    return
  }
  if (incomingRunId > 0 && currentRunId === 0) {
    testStore.activeRunId = incomingRunId
  }

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

  if (payload.latestReportJson || payload.latestReportHtml) {
    testStore.latestReport = {
      ...(testStore.latestReport || {}),
      jsonPath: payload.latestReportJson || testStore.latestReport?.jsonPath || '',
      htmlPath: payload.latestReportHtml || testStore.latestReport?.htmlPath || '',
    }
  }
}

function buildStartPayload() {
  return {
    sourceDir: testStore.sourceDir,
    initialPhase: testStore.phase,
    ...testStore.config,
  }
}

function resetRuntimeViewState() {
  finishedReportRetryCount = 0
  testStore.status = 'idle'
  testStore.phase = 'INIT'
  testStore.currentRound = 0
  resetElapsedSyncState()
  testStore.stopReason = ''
  testStore.lastError = ''
  testStore.activeRunId = 0
  testStore.coverage.current = 0
  testStore.coverage.branch = 0
  testStore.coverage.lineCovered = 0
  testStore.coverage.lineTotal = 0
  testStore.coverage.branchCovered = 0
  testStore.coverage.branchTotal = 0
  testStore.coverage.lastUpdatedAt = 0
  testStore.coverage.history = [{ round: 0, value: 0, ts: Date.now() }]
  testStore.crashes = []
  testStore.messages = []
  testStore.latestReport = {
    report: null,
    jsonPath: '',
    htmlPath: '',
    fetchedAt: '',
  }

  Object.keys(testStore.metrics).forEach((key) => {
    testStore.metrics[key] = 0
  })

  Object.keys(testStore.agents).forEach((name) => {
    testStore.agents[name].status = 'idle'
    testStore.agents[name].messageCount = 0
    testStore.agents[name].lastAction = '等待启动'
  })
}

async function waitUploadingDone(timeoutMs = 12000) {
  const startAt = Date.now()
  while (testStore.uploadingSource) {
    if (Date.now() - startAt > timeoutMs) {
      return false
    }
    await new Promise((resolve) => window.setTimeout(resolve, 120))
  }
  return true
}

async function waitBackendNotRunning(timeoutMs = 12000) {
  const startAt = Date.now()

  while (Date.now() - startAt <= timeoutMs) {
    try {
      const res = await getStatus()
      const data = res?.data || {}
      const running = Boolean(data.running) || data.status === 'running'
      if (!running) {
        return true
      }
    } catch (error) {
      // 状态查询失败时继续重试，避免偶发网络抖动直接中断流程
    }

    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }

  return false
}

async function ensureFreshBackendRunSlot() {
  try {
    const res = await getStatus()
    const data = res?.data || {}
    const running = Boolean(data.running) || data.status === 'running'
    if (!running) return true

    // 发现旧任务仍在运行：先请求停止，再等待后端释放运行槽。
    await stopTest()
    const stopped = await waitBackendNotRunning()
    if (!stopped) {
      ElMessage.error('检测到已有测试任务且未能及时停止，请点击“停止”后重试')
      return false
    }

    return true
  } catch (error) {
    // 若状态接口不可用，交由后续 start 调用统一处理错误提示。
    return true
  }
}

async function ensureSourceDirReady() {
  const files = Array.isArray(testStore.pendingFiles) ? testStore.pendingFiles.filter(Boolean) : []
  const pendingFingerprint = buildFilesFingerprint(files)
  testStore.pendingFilesFingerprint = pendingFingerprint

  // 无待上传文件时，必须已有有效 sourceDir。
  if (files.length === 0) {
    if (testStore.sourceDir) return true
    ElMessage.warning('请先上传 .c/.h 文件，再启动测试')
    return false
  }

  const shouldReuseUploadedDir = Boolean(
    testStore.sourceDir &&
    pendingFingerprint &&
    testStore.syncedFilesFingerprint === pendingFingerprint,
  )
  if (shouldReuseUploadedDir) return true

  // 若左侧正在自动上传，等待其完成后复查
  if (testStore.uploadingSource) {
    const done = await waitUploadingDone()
    const refreshedReuse = Boolean(
      done &&
      testStore.sourceDir &&
      testStore.syncedFilesFingerprint &&
      testStore.syncedFilesFingerprint === pendingFingerprint,
    )
    if (refreshedReuse) return true
  }

  testStore.uploadingSource = true
  try {
    const res = await uploadFiles(files)
    const sourceDir = res?.data?.sourceDir || ''
    if (!sourceDir) {
      ElMessage.error('上传成功但未返回源码目录，请检查后端')
      return false
    }
    testStore.sourceDir = sourceDir
    testStore.syncedFilesFingerprint = pendingFingerprint
    ElMessage.success('源码已自动上传到后端')
    return true
  } catch (error) {
    ElMessage.error('自动上传失败，请检查后端服务')
    return false
  } finally {
    testStore.uploadingSource = false
  }
}

function applyReportToStore(report = {}) {
  if (!report || typeof report !== 'object') return

  const summary = report.summary || {}
  const lineCoverageRatio = Number(summary.line_coverage || 0)
  const lineCoveragePct = Math.max(0, Math.min(100, Number((lineCoverageRatio * 100).toFixed(2))))
  const branchCoverageRatio = Number(summary.branch_coverage || 0)
  const branchCoveragePct = Math.max(0, Math.min(100, Number((branchCoverageRatio * 100).toFixed(2))))

  if (summary.total_source_lines !== undefined) {
    const totalSourceLines = Number(summary.total_source_lines)
    if (Number.isFinite(totalSourceLines) && totalSourceLines >= 0) {
      testStore.totalSourceLines = totalSourceLines
      testStore.coverage.lineTotal = Math.round(totalSourceLines)
    }
  }

  testStore.coverage.current = lineCoveragePct
  testStore.coverage.branch = branchCoveragePct

  if (summary.line_covered !== undefined) {
    const lineCovered = Number(summary.line_covered)
    if (Number.isFinite(lineCovered) && lineCovered >= 0) {
      testStore.coverage.lineCovered = Math.round(lineCovered)
    }
  }

  if (summary.branch_covered !== undefined) {
    const branchCovered = Number(summary.branch_covered)
    if (Number.isFinite(branchCovered) && branchCovered >= 0) {
      testStore.coverage.branchCovered = Math.round(branchCovered)
    }
  }

  if (summary.branch_total !== undefined) {
    const branchTotal = Number(summary.branch_total)
    if (Number.isFinite(branchTotal) && branchTotal >= 0) {
      testStore.coverage.branchTotal = Math.round(branchTotal)
    }
  }

  const nextRound = Math.max(0, Number(testStore.currentRound || 0))
  appendCoverageHistory(nextRound, lineCoveragePct, Date.now())

  testStore.metrics.totalCases = Number(summary.total_executed || 0)
  testStore.metrics.approved = Number(summary.total_passed || 0)
  testStore.metrics.rejected = Math.max(0, Number(summary.total_generated || 0) - Number(summary.total_executed || 0))
  testStore.metrics.falsePositives = Number(summary.false_positives || 0)
  testStore.metrics.uniqueCrashes = Number(summary.confirmed_bugs || 0)

  const bugs = Array.isArray(report.bugs) ? report.bugs : []
  testStore.crashes = bugs.map((item, index) => ({
    id: item.id || `bug_${index}`,
    type: item.type || 'unknown',
    input: item.input || '',
    stackTrace: item.stack_trace || item.stackTrace || item.description || `${item.type || 'unknown'} (rc=${Number(item.return_code || 0)})`,
    description: item.description || '',
    location: item.location || '',
    risk: item.risk || item.risk_summary || item.description || '',
    occurrences: Math.max(1, Number(item.occurrences || item.count || 1)),
    verified: true,
    severity: String(item.risk_level || item.severity || 'medium').toLowerCase(),
  }))

  const interaction = report.agent_interactions || {}
  const bySender = interaction.messages_by_sender || {}
  const byTopic = interaction.messages_by_topic || {}

  // 将报告中的交互统计转换为日志流，供“智能体日志”和“统计图表”标签展示
  const syntheticMessages = Object.entries(byTopic).map(([topic, count], index) => {
    const route = TOPIC_ROUTE_MAP[topic] || { from: 'System', to: 'Broadcast' }
    return {
      id: `report_${index}_${topic}`,
      from: route.from,
      to: route.to,
      type: topic,
      summary: `报告回放：${topic} × ${Number(count || 0)}`,
      timestamp: new Date().toISOString(),
      data: { count: Number(count || 0), source: 'report' },
      count: Number(count || 0),
    }
  })

  if (syntheticMessages.length > 0) {
    const shouldReplaceMessages =
      !Array.isArray(testStore.messages) ||
      testStore.messages.length === 0 ||
      testStore.status === 'finished'

    if (shouldReplaceMessages) {
      testStore.messages = syntheticMessages
    }
  }

  Object.keys(testStore.agents).forEach((name) => {
    const count = Number(bySender[name] || 0)
    testStore.agents[name].messageCount = count
    if (count > 0 && testStore.agents[name].status === 'idle') {
      testStore.agents[name].status = 'waiting'
    }
  })
}

export async function refreshBackendStatus(silent = true) {
  if (testStore.runMode !== 'realtime') return null

  try {
    const res = await getStatus()
    const data = res?.data || {}

    const incomingRunId = Number(data.runId || 0)
    const currentRunId = Number(testStore.activeRunId || 0)
    if (incomingRunId > 0 && currentRunId > 0 && incomingRunId !== currentRunId) {
      return data
    }

    applyStatusPayload(data)

    if (data.status === 'finished') {
      // 报告可能略晚于 finished 状态可见，保持短时重试，拿到报告再断开实时连接。
      if (!testStore.latestReport?.report) {
        const reportData = await fetchLatestReport(false)
        if (reportData?.report) {
          finishedReportRetryCount = 0
        } else {
          finishedReportRetryCount += 1
        }
      }

      const hasReport = Boolean(testStore.latestReport?.report)
      if (hasReport) {
        stopStatusPolling()
        stopRealtimeMode()
      } else if (
        finishedReportRetryCount > 0 &&
        finishedReportRetryCount % MAX_FINISHED_REPORT_RETRY === 0
      ) {
        // 持续等待报告时做节流提示，不提前停轮询，避免“完成后底部一直空白”。
        ElMessage.warning('测试已完成，正在等待报告生成...')
      }
    } else if (data.status === 'error') {
      stopStatusPolling()
      stopRealtimeMode()
    }

    return data
  } catch (error) {
    if (!silent) {
      ElMessage.error('获取后端状态失败，请确认后端服务已启动')
    }
    return null
  }
}

export function startStatusPolling() {
  if (statusTimer) return

  statusTimer = window.setInterval(() => {
    refreshBackendStatus(true)
  }, 800)
}

export function stopStatusPolling() {
  if (statusTimer) {
    window.clearInterval(statusTimer)
    statusTimer = null
  }
}

export async function fetchLatestReport(showToast = true) {
  try {
    const res = await getReport()
    const data = res?.data || {}
    const report = data.report || null

    testStore.latestReport = {
      report,
      jsonPath: data.jsonPath || '',
      htmlPath: data.htmlPath || '',
      fetchedAt: new Date().toISOString(),
    }

    if (report) {
      applyReportToStore(report)
    }

    if (showToast) {
      ElMessage.success('已获取最新测试报告')
    }
    return data
  } catch (error) {
    if (showToast) {
      ElMessage.warning('暂无可用测试报告')
    }
    return null
  }
}

export async function startTestFlow() {
  testStore.startingTest = true
  try {
    const ready = await ensureSourceDirReady()
    if (!ready) {
      throw new Error('sourceDir is empty')
    }

    const freshSlotReady = await ensureFreshBackendRunSlot()
    if (!freshSlotReady) {
      throw new Error('backend run slot busy')
    }

    resetRuntimeViewState()
    startRealtimeMode()
    startStatusPolling()

    const startRes = await startTest(buildStartPayload())
    const runId = Number(startRes?.data?.runId || 0)
    if (Number.isFinite(runId) && runId > 0) {
      testStore.activeRunId = runId
    }

    testStore.status = 'running'
    await refreshBackendStatus(true)
    ElMessage.success('已启动实时测试')
  } catch (error) {
    const statusCode = Number(error?.response?.status || 0)
    const detail = String(error?.response?.data?.detail || '')

    // 若后端已在运行（重复点击“启动”），不要误标记为错误，直接恢复连接并同步状态。
    if (statusCode === 409 && /运行中|already|正在运行/i.test(detail)) {
      await refreshBackendStatus(true)
      if (testStore.status !== 'running') {
        testStore.status = 'running'
      }
      ElMessage.info('测试任务已在运行，已恢复实时连接')
      return
    }

    stopRealtimeMode()
    stopStatusPolling()
    testStore.status = 'error'
    ElMessage.error(detail ? `启动失败：${detail}` : '启动实时测试失败，请检查后端服务')
    throw error
  } finally {
    testStore.startingTest = false
  }
}

export async function stopTestFlow(markIdle = true) {
  testStore.startingTest = false
  try {
    await stopTest()
  } catch (error) {
    // 允许后端已停止等场景
  }

  stopRealtimeMode()
  stopStatusPolling()

  await refreshBackendStatus(true)

  if (markIdle) {
    testStore.status = 'idle'
    testStore.activeRunId = 0
  }
}

export async function pauseTestFlow() {
  // 当前后端未提供真正 pause，先按 stop 处理并标记 paused
  await stopTestFlow(false)
  testStore.status = 'paused'
  ElMessage.info('当前版本后端不支持断点暂停，已停止测试并标记为暂停')
}

export { applyStatusPayload }
