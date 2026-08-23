import { computed, ref } from 'vue'
import { pushToast } from './useUi'
import { dbStats, deviceProfiles, fetchDbStats, fetchProfiles } from './useTasks'

const devicePacks = ref([])
const deviceCatalogMeta = ref({
  total_count: 0,
  is_loaded: false,
  sample_models: [],
  pack_count: 0,
  enabled_packs: 0,
  disabled_packs: 0,
  active_countries: [],
  supported_countries: []
})
const selectedPackId = ref('')
const deviceLoading = ref(false)
const deviceUploading = ref(false)
const deviceUploadDragging = ref(false)
const deviceUploadProgress = ref(0)
const deviceUploadResult = ref(null)
const deviceFileInput = ref(null)
const generateForm = ref({
  country: 'id',
  count: 300,
  alias: '',
  enabled: true
})
const generateBusy = ref(false)
const renameDrafts = ref({})
const countryDrafts = ref({})
const busyPackId = ref('')

const selectedPack = computed(
  () => devicePacks.value.find((item) => item.id === selectedPackId.value) || devicePacks.value[0] || null
)

const applyCatalog = (data) => {
  devicePacks.value = data.packs || []
  deviceCatalogMeta.value = {
    total_count: data.total_count || 0,
    is_loaded: !!data.is_loaded,
    sample_models: data.sample_models || [],
    pack_count: data.pack_count || 0,
    enabled_packs: data.enabled_packs || 0,
    disabled_packs: data.disabled_packs || 0,
    active_countries: data.active_countries || [],
    supported_countries: data.supported_countries || []
  }
  dbStats.value = {
    total_count: data.total_count || 0,
    is_loaded: !!data.is_loaded,
    sample_models: data.sample_models || []
  }
  if (selectedPackId.value && !devicePacks.value.some((item) => item.id === selectedPackId.value)) {
    selectedPackId.value = devicePacks.value[0]?.id || ''
  }
  if (!selectedPackId.value && devicePacks.value[0]) {
    selectedPackId.value = devicePacks.value[0].id
  }
  for (const pack of devicePacks.value) {
    if (renameDrafts.value[pack.id] == null) renameDrafts.value[pack.id] = pack.alias
    if (countryDrafts.value[pack.id] == null) countryDrafts.value[pack.id] = pack.country || ''
  }
}

export const fetchDeviceCatalog = async () => {
  deviceLoading.value = true
  try {
    const res = await fetch('/api/device-dbs')
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '读取硬件指纹目录失败')
    applyCatalog(data)
  } catch (e) {
    console.error('Fetch device catalog error:', e)
    pushToast('danger', `读取硬件指纹目录失败: ${e.message}`)
  } finally {
    deviceLoading.value = false
  }
}

const isAllowedDeviceUpload = (file) => {
  if (!file || !file.name) return false
  return /\.(db|sqlite|sqlite3)$/i.test(file.name)
}

const uploadDeviceFile = (file) => new Promise((resolve, reject) => {
  const xhr = new XMLHttpRequest()
  xhr.open('POST', '/api/device-dbs/upload')
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      deviceUploadProgress.value = Math.max(1, Math.round((event.loaded / event.total) * 90))
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
      deviceUploadProgress.value = 100
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

export const handleDeviceUpload = async (file) => {
  if (!file) return
  if (!isAllowedDeviceUpload(file)) {
    deviceUploadResult.value = { success: false, message: '仅支持 .db / .sqlite / .sqlite3' }
    pushToast('danger', '仅支持 SQLite 硬件指纹库')
    return
  }
  deviceUploading.value = true
  deviceUploadProgress.value = 1
  deviceUploadResult.value = null
  try {
    const data = await uploadDeviceFile(file)
    deviceUploadResult.value = data
    if (data.pack?.id) selectedPackId.value = data.pack.id
    pushToast('ok', data.message || '硬件指纹包已导入')
    await fetchDeviceCatalog()
  } catch (e) {
    deviceUploadResult.value = { success: false, message: e.message }
    pushToast('danger', `导入失败: ${e.message}`)
  } finally {
    deviceUploading.value = false
  }
}

export const onDeviceFilePicked = (event) => {
  const file = event.target?.files?.[0]
  event.target.value = ''
  handleDeviceUpload(file)
}

export const onDeviceFileDrop = (event) => {
  deviceUploadDragging.value = false
  const file = event.dataTransfer?.files?.[0]
  handleDeviceUpload(file)
}

export const updateDevicePack = async (packId, payload) => {
  busyPackId.value = packId
  try {
    const res = await fetch(`/api/device-dbs/${packId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '更新失败')
    pushToast('ok', data.message || '已更新')
    await fetchDeviceCatalog()
    return data.pack
  } catch (e) {
    pushToast('danger', `更新失败: ${e.message}`)
    return null
  } finally {
    busyPackId.value = ''
  }
}

export const toggleDevicePack = async (pack, enabled) => {
  busyPackId.value = pack.id
  try {
    const res = await fetch(`/api/device-dbs/${pack.id}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '切换失败')
    pushToast('ok', data.message || (enabled ? '已启用' : '已停用'))
    await fetchDeviceCatalog()
  } catch (e) {
    pushToast('danger', `启停失败: ${e.message}`)
  } finally {
    busyPackId.value = ''
  }
}

export const deleteDevicePack = async (pack) => {
  if (!pack?.id) return
  if (!window.confirm(`确认删除「${pack.alias}」？磁盘上的 .db 也会被移除。`)) return
  busyPackId.value = pack.id
  try {
    const res = await fetch(`/api/device-dbs/${pack.id}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '删除失败')
    pushToast('ok', data.message || '已删除')
    if (selectedPackId.value === pack.id) selectedPackId.value = ''
    await fetchDeviceCatalog()
  } catch (e) {
    pushToast('danger', `删除失败: ${e.message}`)
  } finally {
    busyPackId.value = ''
  }
}

export const generateDevicePack = async () => {
  generateBusy.value = true
  try {
    const payload = {
      country: generateForm.value.country,
      count: Number(generateForm.value.count) || 300,
      enabled: !!generateForm.value.enabled
    }
    if (generateForm.value.alias.trim()) payload.alias = generateForm.value.alias.trim()
    const res = await fetch('/api/device-dbs/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '合成失败')
    if (data.pack?.id) selectedPackId.value = data.pack.id
    pushToast('ok', data.message || '已合成新的国家指纹库')
    await fetchDeviceCatalog()
  } catch (e) {
    pushToast('danger', `合成失败: ${e.message}`)
  } finally {
    generateBusy.value = false
  }
}

export const percentOf = (value, total) => {
  if (!total) return 0
  return Math.max(2, Math.round((Number(value) || 0) * 100 / total))
}

export const countryFlag = (code) => ({
  cl: '🇨🇱', id: '🇮🇩', in: '🇮🇳', ru: '🇷🇺', kz: '🇰🇿',
  af: '🇦🇫', us: '🇺🇸', gb: '🇬🇧', br: '🇧🇷', tr: '🇹🇷'
}[String(code || '').toLowerCase()] || '🌐')

export const useDevices = () => ({
  deviceProfiles,
  dbStats,
  devicePacks,
  deviceCatalogMeta,
  selectedPackId,
  selectedPack,
  deviceLoading,
  deviceUploading,
  deviceUploadDragging,
  deviceUploadProgress,
  deviceUploadResult,
  deviceFileInput,
  generateForm,
  generateBusy,
  renameDrafts,
  countryDrafts,
  busyPackId,
  fetchDbStats,
  fetchProfiles,
  fetchDeviceCatalog,
  handleDeviceUpload,
  onDeviceFilePicked,
  onDeviceFileDrop,
  updateDevicePack,
  toggleDevicePack,
  deleteDevicePack,
  generateDevicePack,
  percentOf,
  countryFlag
})
