import { computed, reactive, ref } from 'vue'
import { useConfig } from './useConfig'
import { pushToast } from './useUi'

const { config } = useConfig()

const vaultLoading = ref(false)
const vaultAccounts = ref([])
const vaultMeta = reactive({
  lod_user_dir: '',
  sessions_dir: '',
  published_api_id_count: 0,
  missing_session_count: 0
})
const vaultSelectedId = ref('')
const vaultApplyingId = ref('')
const vaultApplyResult = ref(null)
const vaultGuidance = ref('')
const vaultFileInput = ref(null)
const vaultUploading = ref(false)
const vaultUploadDragging = ref(false)
const vaultUploadProgress = ref(0)
const vaultUploadResult = ref(null)
const appsStarting = ref(false)
const appsJob = ref(null)
const appsShortname = ref('')
const appsPhone = ref('')
const appsManualCode = ref('')

const selectedVaultAccount = computed(
  () => vaultAccounts.value.find((acc) => acc.account_id === vaultSelectedId.value) || null
)

let appsPollTimer = null

const isAllowedVaultUpload = (file) => {
  if (!file || !file.name) return false
  return /\.(zip|session|json)$/i.test(file.name)
}

const uploadVaultFile = (file) => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/vault/upload')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        vaultUploadProgress.value = Math.max(1, Math.round((event.loaded / event.total) * 90))
      }
    }
    xhr.onload = () => {
      let data = {}
      try {
        data = JSON.parse(xhr.responseText || '{}')
      } catch {
        reject(new Error('服务器返回了无法解析的响应'))
        return
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        vaultUploadProgress.value = 100
        resolve(data)
        return
      }
      reject(new Error(data.detail || data.message || `上传失败 HTTP ${xhr.status}`))
    }
    xhr.onerror = () => reject(new Error('网络错误，上传未完成'))
    const body = new FormData()
    body.append('file', file)
    xhr.send(body)
  })
}

export const handleVaultUpload = async (file) => {
  if (!file) return
  if (!isAllowedVaultUpload(file)) {
    vaultUploadResult.value = { success: false, message: '仅支持 .zip / .session / .json' }
    return
  }
  vaultUploading.value = true
  vaultUploadProgress.value = 1
  vaultUploadResult.value = null
  try {
    const data = await uploadVaultFile(file)
    vaultUploadResult.value = data
    await fetchVaultAccounts()
    const first = (data.imported_accounts || [])[0]
    if (first?.account_id) vaultSelectedId.value = first.account_id
    pushToast('ok', data.message || '账号文件已导入凭证库')
  } catch (e) {
    vaultUploadResult.value = { success: false, message: e.message }
    pushToast('danger', e.message)
  } finally {
    vaultUploading.value = false
    if (vaultFileInput.value) vaultFileInput.value.value = ''
  }
}

export const onVaultFilePicked = async (event) => {
  await handleVaultUpload(event.target.files?.[0])
}

export const onVaultFileDrop = async (event) => {
  vaultUploadDragging.value = false
  await handleVaultUpload(event.dataTransfer?.files?.[0])
}

export const fetchVaultAccounts = async () => {
  vaultLoading.value = true
  try {
    const res = await fetch('/api/vault/accounts')
    const data = await res.json()
    vaultAccounts.value = data.accounts || []
    vaultMeta.lod_user_dir = data.lod_user_dir || ''
    vaultMeta.sessions_dir = data.sessions_dir || ''
    vaultMeta.published_api_id_count = data.published_api_id_count || 0
    vaultMeta.missing_session_count = data.missing_session_count || 0
    vaultGuidance.value = data.guidance || ''
    if (data.applied_api_id) config.custom_api_id = data.applied_api_id
    if (data.applied_api_hash) config.custom_api_hash = data.applied_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    if (!vaultSelectedId.value && vaultAccounts.value.length) {
      vaultSelectedId.value = vaultAccounts.value[0].account_id
    }
  } catch (e) {
    console.error('Fetch vault accounts error:', e)
    pushToast('danger', `扫描凭证库失败: ${e.message}`)
  } finally {
    vaultLoading.value = false
  }
}

export const applyVaultCredentials = async (acc) => {
  vaultApplyingId.value = acc.account_id
  vaultApplyResult.value = null
  try {
    const res = await fetch('/api/vault/accounts/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: acc.account_id, set_mode_custom: true })
    })
    const data = await res.json()
    if (!res.ok) {
      vaultApplyResult.value = { success: false, message: data.detail || '应用失败' }
      pushToast('danger', data.detail || '应用失败')
      return
    }
    vaultApplyResult.value = data
    if (data.custom_api_id) config.custom_api_id = data.custom_api_id
    if (data.custom_api_hash) config.custom_api_hash = data.custom_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    pushToast('ok', data.message || '专属凭证已写入全局配置')
  } catch (e) {
    vaultApplyResult.value = { success: false, message: e.message }
    pushToast('danger', e.message)
  } finally {
    vaultApplyingId.value = ''
  }
}

export const stopAppsPoll = () => {
  if (appsPollTimer) {
    clearInterval(appsPollTimer)
    appsPollTimer = null
  }
}

export const pollAppsJob = async (jobId) => {
  stopAppsPoll()
  const tick = async () => {
    try {
      const res = await fetch(`/api/vault/apps/jobs/${jobId}`)
      if (!res.ok) return
      const data = await res.json()
      appsJob.value = data
      if (['success', 'failed'].includes(data.status) || (data.needs_manual_code && data.status === 'waiting_code')) {
        stopAppsPoll()
      }
      if (data.applied_to_config && data.api_id) {
        config.custom_api_id = data.api_id
        config.custom_api_hash = data.api_hash
        config.api_credential_mode = 'custom'
      }
    } catch (e) {
      console.error('Poll apps job error:', e)
    }
  }
  await tick()
  appsPollTimer = setInterval(tick, 1500)
}

export const startAppsJob = async () => {
  const phone = (appsPhone.value || '').trim()
  if (!vaultSelectedId.value && !phone) return
  appsStarting.value = true
  appsManualCode.value = ''
  try {
    const res = await fetch('/api/vault/apps/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_id: vaultSelectedId.value || undefined,
        phone: phone || undefined,
        auto_read_code: true,
        app_shortname: appsShortname.value || undefined,
        apply_to_config: false
      })
    })
    const data = await res.json()
    if (!res.ok) {
      appsJob.value = {
        job_id: '-',
        status: 'failed',
        logs: [data.detail || '发起申请失败'],
        error: data.detail
      }
      pushToast('danger', data.detail || '发起申请失败')
      return
    }
    appsJob.value = data
    await pollAppsJob(data.job_id)
  } catch (e) {
    appsJob.value = { job_id: '-', status: 'failed', logs: [e.message], error: e.message }
    pushToast('danger', e.message)
  } finally {
    appsStarting.value = false
  }
}

export const selectAndStartApps = async (acc) => {
  vaultSelectedId.value = acc.account_id
  await startAppsJob()
}

export const submitAppsCode = async () => {
  if (!appsJob.value?.job_id || !appsManualCode.value) return
  try {
    const res = await fetch('/api/vault/apps/submit-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: appsJob.value.job_id,
        code: appsManualCode.value,
        apply_to_config: false
      })
    })
    const data = await res.json()
    if (!res.ok) {
      pushToast('danger', data.detail || '提交验证码失败')
      return
    }
    appsJob.value = data
    await pollAppsJob(data.job_id)
  } catch (e) {
    pushToast('danger', e.message)
  }
}

export const applyAppsJob = async () => {
  if (!appsJob.value?.job_id) return
  try {
    const res = await fetch('/api/vault/apps/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: appsJob.value.job_id, set_mode_custom: true })
    })
    const data = await res.json()
    if (!res.ok) {
      vaultApplyResult.value = { success: false, message: data.detail || '写入失败' }
      pushToast('danger', data.detail || '写入失败')
      return
    }
    vaultApplyResult.value = data
    if (data.custom_api_id) config.custom_api_id = data.custom_api_id
    if (data.custom_api_hash) config.custom_api_hash = data.custom_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    if (appsJob.value) appsJob.value.applied_to_config = true
    pushToast('ok', data.message || '申请结果已写入 config.json')
  } catch (e) {
    vaultApplyResult.value = { success: false, message: e.message }
    pushToast('danger', e.message)
  }
}

export const useVault = () => ({
  vaultLoading,
  vaultAccounts,
  vaultMeta,
  vaultSelectedId,
  vaultApplyingId,
  vaultApplyResult,
  vaultGuidance,
  vaultFileInput,
  vaultUploading,
  vaultUploadDragging,
  vaultUploadProgress,
  vaultUploadResult,
  selectedVaultAccount,
  appsStarting,
  appsJob,
  appsShortname,
  appsPhone,
  appsManualCode,
  handleVaultUpload,
  onVaultFilePicked,
  onVaultFileDrop,
  fetchVaultAccounts,
  applyVaultCredentials,
  pollAppsJob,
  stopAppsPoll,
  startAppsJob,
  selectAndStartApps,
  submitAppsCode,
  applyAppsJob
})
