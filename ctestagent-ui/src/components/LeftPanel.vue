<script setup>
import { computed, ref } from 'vue'
import { UploadFilled, Delete } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { testStore } from '../stores/testStore'
import { uploadFiles } from '../api/http'

// 上传文件列表
const uploadedFiles = ref([])
const uploading = ref(false)
let autoUploadTimer = null

function buildFilesFingerprint(files = []) {
  return files
    .map((file) => `${String(file?.name || '')}:${Number(file?.size || 0)}:${Number(file?.lastModified || 0)}`)
    .sort()
    .join('|')
}

const sourceSynced = computed(() => {
  return Boolean(
    testStore.sourceDir &&
    testStore.pendingFilesFingerprint &&
    testStore.pendingFilesFingerprint === testStore.syncedFilesFingerprint,
  )
})

function isValidCExtension(fileName = '') {
  const lower = fileName.toLowerCase()
  return lower.endsWith('.c') || lower.endsWith('.h')
}

function onUploadChange(uploadFile) {
  const raw = uploadFile?.raw
  if (!raw) return

  if (!isValidCExtension(raw.name)) {
    ElMessage.warning('仅支持上传 .c / .h 文件')
    return
  }

  const exists = uploadedFiles.value.some((item) => item.name === raw.name && item.size === raw.size)
  if (exists) return

  uploadedFiles.value.push({
    uid: uploadFile.uid,
    name: raw.name,
    size: raw.size,
    raw,
  })

  syncPendingFiles()
  scheduleAutoUpload()
}

function removeUploadedFile(uid) {
  uploadedFiles.value = uploadedFiles.value.filter((item) => item.uid !== uid)
  syncPendingFiles()

  // 文件列表发生变化时，旧目录失效，后续需重新上传
  testStore.sourceDir = ''
  testStore.syncedFilesFingerprint = ''
}

function syncPendingFiles() {
  const files = uploadedFiles.value.map((item) => item.raw).filter(Boolean)
  testStore.pendingFiles = files
  testStore.pendingFilesFingerprint = buildFilesFingerprint(files)
}

function scheduleAutoUpload() {
  if (autoUploadTimer) {
    window.clearTimeout(autoUploadTimer)
  }

  autoUploadTimer = window.setTimeout(async () => {
    if (uploadedFiles.value.length === 0) return
    await uploadToBackend({ silentSuccess: true })
  }, 350)
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

async function uploadToBackend(options = {}) {
  const { silentSuccess = false } = options
  const files = uploadedFiles.value.map((item) => item.raw).filter(Boolean)
  if (files.length === 0) {
    ElMessage.warning('请先选择要上传的 .c/.h 文件')
    return false
  }

  if (uploading.value) return false

  uploading.value = true
  testStore.uploadingSource = true
  try {
    testStore.pendingFiles = files
    const res = await uploadFiles(files)
    const sourceDir = res?.data?.sourceDir || ''
    if (sourceDir) {
      testStore.sourceDir = sourceDir
      testStore.syncedFilesFingerprint = testStore.pendingFilesFingerprint
      if (!silentSuccess) {
        ElMessage.success(`上传成功：${files.length} 个文件`)
      }
      return true
    }

    ElMessage.error('上传成功但未返回源码目录，请检查后端')
    return false
  } catch (error) {
    ElMessage.error('上传失败，请检查后端服务或文件格式')
    return false
  } finally {
    uploading.value = false
    testStore.uploadingSource = false
  }
}

</script>

<template>
  <div class="left-panel-inner">
    <div class="section-title">上传被测 C 文件</div>

    <el-upload
      drag
      multiple
      :auto-upload="false"
      :show-file-list="false"
      accept=".c,.h"
      class="upload-zone"
      @change="onUploadChange"
    >
      <el-icon class="upload-icon"><UploadFilled /></el-icon>
      <div class="el-upload__text">将 .c / .h 文件拖拽到此处，或 <em>点击上传</em></div>
    </el-upload>

    <div class="file-list" v-if="uploadedFiles.length > 0">
      <div class="file-item" v-for="file in uploadedFiles" :key="file.uid">
        <div class="file-meta">
          <span class="file-name">{{ file.name }}</span>
          <span class="file-size">{{ formatFileSize(file.size) }}</span>
        </div>
        <el-button
          type="danger"
          text
          :icon="Delete"
          @click="removeUploadedFile(file.uid)"
        >
          删除
        </el-button>
      </div>
    </div>

    <div v-if="uploadedFiles.length > 0" class="upload-actions">
      <el-button size="small" type="primary" :loading="uploading" @click="uploadToBackend()">
        立即上传到后端
      </el-button>
      <el-tag size="small" :type="sourceSynced ? 'success' : 'warning'">
        {{ sourceSynced ? '后端已同步' : '待同步' }}
      </el-tag>
    </div>

    <div class="source-dir" v-if="testStore.sourceDir">
      当前上传目录：{{ testStore.sourceDir }}
    </div>

    <div class="upload-tip" v-if="uploadedFiles.length > 0">
      文件上传完成后，请点击顶部导航栏中的“启动”开始测试。
    </div>
  </div>
</template>

<style scoped>
.left-panel-inner {
  height: 100%;
  padding: 10px;
  background: rgba(15, 20, 35, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}

.section-title {
  margin-bottom: 10px;
  color: #e8f3ff;
  font-size: 14px;
  font-weight: 600;
}

.upload-zone {
  width: 100%;
}

:deep(.upload-zone .el-upload-dragger) {
  width: 100%;
  border: 1px dashed rgba(0, 212, 255, 0.6);
  background: rgba(7, 13, 24, 0.72);
}

.upload-icon {
  font-size: 28px;
  color: #00d4ff;
}

.file-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border: 1px solid rgba(0, 212, 255, 0.2);
  border-radius: 8px;
  background: rgba(10, 18, 31, 0.65);
}

.file-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.file-name {
  color: #e7f3ff;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 170px;
}

.file-size {
  color: #8ca5cf;
  font-size: 12px;
}

.source-dir {
  margin-top: 8px;
  font-size: 12px;
  color: #8fdcff;
  word-break: break-all;
}

.upload-tip {
  margin-top: 10px;
  padding: 10px;
  border-radius: 8px;
  background: rgba(0, 212, 255, 0.08);
  color: #bfefff;
  font-size: 12px;
  line-height: 1.5;
}

.upload-actions {
  margin-top: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
</style>
