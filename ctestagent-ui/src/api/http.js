import axios from 'axios'

function resolveApiBaseURL() {
  const envBase = String(import.meta.env.VITE_API_BASE_URL || '').trim()
  if (envBase) {
    return envBase.replace(/\/$/, '')
  }

  if (typeof window !== 'undefined' && window.location?.origin) {
    const { origin, protocol } = window.location
    if (protocol === 'http:' || protocol === 'https:') {
      return `${origin}/api`
    }
  }

  return 'http://localhost:8000/api'
}

export const API_BASE_URL = resolveApiBaseURL()

// axios 实例
export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

/** 上传被测源码文件（.c/.h） */
export function uploadFiles(files = []) {
  const formData = new FormData()
  files.forEach((file) => {
    if (!file) return
    formData.append('files', file)
  })
  return http.post('/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
}

/** 启动测试 */
export function startTest(config = {}) {
  return http.post('/start', config)
}

/** 停止测试 */
export function stopTest() {
  return http.post('/stop')
}

/** 获取当前运行状态 */
export function getStatus() {
  return http.get('/status')
}

/** 获取测试报告 */
export function getReport() {
  return http.get('/report', {
    timeout: 60000,
  })
}
