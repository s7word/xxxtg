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
  missing_session_count: 0,
  active_probe_count: 0,
  usable_count: 0,
  useless_count: 0
})
const vaultSelectedId = ref('')
const vaultSelectedIds = ref([])
const vaultFilter = ref('all')
const vaultBusy = ref('')
const vaultApplyingId = ref('')
const vaultApplyResult = ref(null)
const vaultGuidance = ref('')
const vaultProbeTogglingId = ref('')
const activeProbeCount = computed(
  () => vaultAccounts.value.filter((acc) => acc.is_probe_active).length
)
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

const filteredVaultAccounts = computed(() => {
  if (vaultFilter.value === 'usable') return vaultAccounts.value.filter((acc) => acc.usable)
  if (vaultFilter.value === 'useless') return vaultAccounts.value.filter((acc) => acc.useless)
  return vaultAccounts.value
})

const allVisibleVaultSelected = computed(() => {
  const ids = filteredVaultAccounts.value.map((acc) => acc.account_id)
  return ids.length > 0 && ids.every((id) => vaultSelectedIds.value.includes(id))
})

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
    vaultMeta.active_probe_count = data.active_probe_count || activeProbeCount.value
    vaultMeta.usable_count = data.usable_count || 0
    vaultMeta.useless_count = data.useless_count || 0
    vaultGuidance.value = data.guidance || ''
    if (data.applied_api_id) config.custom_api_id = data.applied_api_id
    if (data.applied_api_hash) config.custom_api_hash = data.applied_api_hash
    if (data.api_credential_mode) config.api_credential_mode = data.api_credential_mode
    if (!vaultSelectedId.value && vaultAccounts.value.length) {
      vaultSelectedId.value = vaultAccounts.value[0].account_id
    }
    const known = new Set(vaultAccounts.value.map((acc) => acc.account_id))
    vaultSelectedIds.value = vaultSelectedIds.value.filter((id) => known.has(id))
  } catch (e) {
    console.error('Fetch vault accounts error:', e)
    pushToast('danger', `扫描凭证库失败: ${e.message}`)
  } finally {
    vaultLoading.value = false
  }
}

export const toggleVaultProbe = async (acc, nextActive) => {
  if (!acc?.account_id) return
  if (!acc.session_valid) {
    pushToast('danger', '该账号没有有效 .session，无法作为预检探针')
    return
  }
  const active = typeof nextActive === 'boolean' ? nextActive : !acc.is_probe_active
  vaultProbeTogglingId.value = acc.account_id
  try {
    const res = await fetch('/api/vault/accounts/toggle-probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: acc.account_id, active })
    })
    const data = await res.json()
    if (!res.ok || !data.success) {
      pushToast('danger', data.detail || data.message || '切换预检探针失败')
      return
    }
    acc.is_probe_active = data.is_probe_active
    vaultAccounts.value = vaultAccounts.value.map((item) => (
      item.account_id === acc.account_id
        ? { ...item, is_probe_active: data.is_probe_active }
        : item
    ))
    pushToast('ok', data.message || (active ? '已激活预检探针' : '已停用预检探针'))
    await fetchVaultAccounts()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    vaultProbeTogglingId.value = ''
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

export const uselessReasonLabel = (acc) => {
  if (!acc?.useless) return '可用'
  if (acc.useless_reason === 'json_only') return '仅 JSON'
  if (acc.useless_reason === 'invalid_session') return 'session 损坏'
  if (acc.useless_reason === 'incomplete_session') return '未完成注册'
  if (acc.useless_reason === 'empty') return '空记录'
  return '无用'
}

export const toggleVaultAccount = (accountId) => {
  if (!accountId) return
  if (vaultSelectedIds.value.includes(accountId)) {
    vaultSelectedIds.value = vaultSelectedIds.value.filter((id) => id !== accountId)
    return
  }
  vaultSelectedIds.value = [...vaultSelectedIds.value, accountId]
}

export const toggleSelectVisibleVault = () => {
  const ids = filteredVaultAccounts.value.map((acc) => acc.account_id)
  if (allVisibleVaultSelected.value) {
    const drop = new Set(ids)
    vaultSelectedIds.value = vaultSelectedIds.value.filter((id) => !drop.has(id))
    return
  }
  vaultSelectedIds.value = Array.from(new Set([...vaultSelectedIds.value, ...ids]))
}

const triggerZipDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename || 'edgenode-accounts.zip'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export const exportVaultAccounts = async ({ accountIds = [], scope = 'selected' } = {}) => {
  vaultBusy.value = 'export'
  try {
    const res = await fetch('/api/vault/accounts/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ account_ids: accountIds, scope })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.detail || data.message || `导出失败 HTTP ${res.status}`)
    }
    const blob = await res.blob()
    const named = res.headers.get('X-Vault-Export-Filename')
    const disposition = res.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="?([^"]+)"?/)
    triggerZipDownload(blob, named || (match && match[1]) || 'edgenode-accounts.zip')
    pushToast('ok', '凭证 ZIP 已开始下载')
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    vaultBusy.value = ''
  }
}

export const deleteVaultAccounts = async ({ accountIds = [], scope = 'selected', confirmText } = {}) => {
  const hint = confirmText || '确认删除这些凭证文件？此操作不可恢复。'
  if (!window.confirm(hint)) return
  vaultBusy.value = 'delete'
  try {
    const res = await fetch('/api/vault/accounts/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ account_ids: accountIds, scope })
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || !data.success) {
      throw new Error(data.detail || data.message || '删除失败')
    }
    vaultSelectedIds.value = []
    pushToast('ok', data.message || '凭证已删除')
    await fetchVaultAccounts()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    vaultBusy.value = ''
  }
}

export const useVault = () => ({
  vaultLoading,
  vaultAccounts,
  vaultMeta,
  vaultSelectedId,
  vaultSelectedIds,
  vaultFilter,
  vaultBusy,
  vaultApplyingId,
  vaultApplyResult,
  vaultGuidance,
  vaultProbeTogglingId,
  activeProbeCount,
  vaultFileInput,
  vaultUploading,
  vaultUploadDragging,
  vaultUploadProgress,
  vaultUploadResult,
  selectedVaultAccount,
  filteredVaultAccounts,
  allVisibleVaultSelected,
  appsStarting,
  appsJob,
  appsShortname,
  appsPhone,
  appsManualCode,
  handleVaultUpload,
  onVaultFilePicked,
  onVaultFileDrop,
  fetchVaultAccounts,
  toggleVaultProbe,
  applyVaultCredentials,
  pollAppsJob,
  stopAppsPoll,
  startAppsJob,
  selectAndStartApps,
  submitAppsCode,
  applyAppsJob,
  uselessReasonLabel,
  toggleVaultAccount,
  toggleSelectVisibleVault,
  exportVaultAccounts,
  deleteVaultAccounts
})
