import { reactive } from 'vue'

/**
 * 多智能体测试全局状态（前端演示版）
 */
export const testStore = reactive({
  // 运行模式：固定 realtime（真实后端）
  runMode: 'realtime',

  // 测试进程状态
  status: 'idle', // 'idle' | 'running' | 'paused' | 'finished'
  phase: 'INIT', // 'INIT' | 'EXPLORE' | 'DEEP' | 'FINALIZE'
  currentRound: 0,
  maxRounds: 500,
  elapsedSeconds: 0,
  elapsedSyncServerSeconds: 0,
  elapsedSyncClientMs: 0,
  elapsedSyncRunId: 0,
  stopReason: '',
  lastError: '',
  totalSourceLines: 0,
  activeRunId: 0,
  startingTest: false,

  // 被测项目目录（由上传接口返回）
  sourceDir: '',
  pendingFiles: [],
  uploadingSource: false,
  pendingFilesFingerprint: '',
  syncedFilesFingerprint: '',
  entryFunction: '',
  compileFlags: '',

  // 最近报告缓存
  latestReport: {
    report: null,
    jsonPath: '',
    htmlPath: '',
    fetchedAt: '',
  },

  // 实时连接状态
  realtime: {
    connected: false,
    reconnectAttempts: 0,
    maxReconnectAttempts: 5,
    lastError: '',
  },

  // 六大智能体状态
  agents: {
    Coordinator: {
      name: 'Coordinator',
      status: 'idle', // 'idle' | 'working' | 'waiting'
      messageCount: 0,
      lastAction: '等待启动',
    },
    Analyzer: {
      name: 'Analyzer',
      status: 'idle',
      messageCount: 0,
      lastAction: '等待启动',
    },
    Generator: {
      name: 'Generator',
      status: 'idle',
      messageCount: 0,
      lastAction: '等待启动',
    },
    Executor: {
      name: 'Executor',
      status: 'idle',
      messageCount: 0,
      lastAction: '等待启动',
    },
    Reviewer: {
      name: 'Reviewer',
      status: 'idle',
      messageCount: 0,
      lastAction: '等待启动',
    },
    Reporter: {
      name: 'Reporter',
      status: 'idle',
      messageCount: 0,
      lastAction: '等待启动',
    },
  },

  // 消息流
  messages: [],

  // 覆盖率
  coverage: {
    current: 0,
    branch: 0,
    lineCovered: 0,
    lineTotal: 0,
    branchCovered: 0,
    branchTotal: 0,
    lastUpdatedAt: 0,
    history: [{ round: 0, value: 0, ts: 0 }],
    target: 100,
  },

  // 缺陷列表
  crashes: [],

  // 运行统计
  metrics: {
    totalCases: 0,
    approved: 0,
    rejected: 0,
    falsePositives: 0,
    uniqueCrashes: 0,
  },

  // 测试配置
  config: {
    batchSize: 60,
    timeout: 3,
    maxRounds: 500,
    maxRoundsHardCap: 800,
    maxTime: 30,
    noProgressStopEnabled: false,
    noProgressRoundLimit: 30,
    noProgressMinRoundsBeforeStop: 240,
    noProgressMinSecondsBeforeStop: 900,
    noProgressRescueMaxAttempts: 0,
    noProgressRescueCooldownRounds: 30,
    maxBatchSize: 240,
    rescueBatchMultiplier: 2.0,
    stagnationBatchMultiplier: 1.5,
    coverageGoalLine: 100,
    coverageGoalBranch: 100,
    strategyWeights: {
      random: 0.2,
      boundary: 0.35,
      grammar: 0.35,
      coverageGuided: 0.1,
    },
    reviewerThreshold: 0.4,
    crashVerifyCount: 3,
    reviewerWeights: {
      diversity: 0.3,
      coverage: 0.4,
      validity: 0.3,
    },
    autoPhaseCoverageThreshold: 60,
  },
})
