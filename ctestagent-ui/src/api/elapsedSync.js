import { testStore } from '../stores/testStore'

const ELAPSED_DRIFT_TOLERANCE_SECONDS = 2

function toSafeRunId(value) {
  const runId = Number(value || 0)
  return Number.isFinite(runId) && runId > 0 ? runId : 0
}

export function resetElapsedSyncState() {
  testStore.elapsedSeconds = 0
  testStore.elapsedSyncServerSeconds = 0
  testStore.elapsedSyncClientMs = 0
  testStore.elapsedSyncRunId = 0
}

export function applyElapsedSecondsFromServer(rawElapsed, options = {}) {
  const incoming = Number(rawElapsed)
  if (!Number.isFinite(incoming) || incoming < 0) return

  const nowMs = Date.now()
  const incomingSeconds = Math.max(0, Math.floor(incoming))

  const payloadRunId = toSafeRunId(options.runId)
  const activeRunId = toSafeRunId(testStore.activeRunId)
  const nextRunId = payloadRunId || activeRunId

  const lastRunId = toSafeRunId(testStore.elapsedSyncRunId)
  const currentElapsed = Math.max(0, Number(testStore.elapsedSeconds || 0))
  const lastServerElapsed = Math.max(
    0,
    Number(testStore.elapsedSyncServerSeconds || currentElapsed || 0),
  )
  const lastSyncMs = Math.max(0, Number(testStore.elapsedSyncClientMs || 0))

  const status = String(options.status || testStore.status || '').toLowerCase()

  const shouldResetAnchor =
    currentElapsed <= 0 ||
    lastSyncMs <= 0 ||
    incomingSeconds < currentElapsed ||
    (nextRunId > 0 && lastRunId > 0 && nextRunId !== lastRunId) ||
    status === 'idle'

  if (shouldResetAnchor) {
    testStore.elapsedSeconds = incomingSeconds
    testStore.elapsedSyncServerSeconds = incomingSeconds
    testStore.elapsedSyncClientMs = nowMs
    testStore.elapsedSyncRunId = nextRunId
    return
  }

  const wallDeltaSeconds = Math.max(0, (nowMs - lastSyncMs) / 1000)
  const maxReasonableElapsed = lastServerElapsed + wallDeltaSeconds + ELAPSED_DRIFT_TOLERANCE_SECONDS
  const clampedIncoming = Math.min(incomingSeconds, Math.floor(maxReasonableElapsed))

  const nextElapsed = Math.max(currentElapsed, clampedIncoming)

  testStore.elapsedSeconds = nextElapsed
  testStore.elapsedSyncServerSeconds = nextElapsed
  testStore.elapsedSyncClientMs = nowMs
  testStore.elapsedSyncRunId = nextRunId
}
