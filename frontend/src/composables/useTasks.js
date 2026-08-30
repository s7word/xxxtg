import { computed, nextTick, ref } from 'vue'
import { useConfig } from './useConfig'
import { useProxy } from './useProxy'
import { detailTask, pushToast } from './useUi'

const { config, form } = useConfig()
const { matchedProxy, previewAutoSelect } = useProxy()

const batchMode = ref(false)
const batchCount = ref(3)
const batchConcurrency = ref(3)
const huntMode = ref(false)
const huntAttempts = ref(100)
const currentBatch = ref(null)
const taskFilter = ref('all')
const selectedTaskIds = ref([])
const mergedLogView = ref(false)
const isStartingTask = ref(false)
const startError = ref('')
export const activeTask = ref(null)
export const taskList = ref([])
const sessions = ref([])
export const deviceProfiles = ref([])
export const dbStats = ref({ total_count: 0, is_loaded: false, sample_models: [] })
const terminalRef = ref(null)
const phonePrecheckStatus = ref({
  enabled: true,
  active: false,
  probe_count: 0,
  probe_phones: [],
  degraded: true,
  message: ''
})

const effectiveConcurrency = computed(() =>
  Math.max(1, Math.min(Number(batchConcurrency.value) || 1, Number(batchCount.value) || 1))
)

const visibleTaskList = computed(() => {
  if (taskFilter.value === 'batch' && currentBatch.value?.batch_id) {
    const ids = new Set(currentBatch.value.task_ids || [])
    return taskList.value.filter((t) => ids.has(t.task_id) || t.batch_id === currentBatch.value.batch_id)
  }
  return taskList.value
})

const allVisibleSelected = computed(() => {
  const ids = visibleTaskList.value.map((t) => t.task_id)
  return ids.length > 0 && ids.every((id) => selectedTaskIds.value.includes(id))
})

const batchStats = computed(() => {
  const ids = new Set(currentBatch.value?.task_ids || [])
  const items = taskList.value.filter((t) => ids.has(t.task_id) || (currentBatch.value && t.batch_id === currentBatch.value.batch_id))
  return {
    success: currentBatch.value?.success ?? items.filter((t) => t.status === 'success').length,
    failed: currentBatch.value?.failed ?? items.filter((t) => t.status === 'failed' || t.status === 'filtered').length,
    running: currentBatch.value?.running ?? items.filter((t) => t.status === 'running').length,
    pending: currentBatch.value?.pending ?? items.filter((t) => t.status === 'pending' || !t.status).length,
    precheck: currentBatch.value?.precheck_intercepted
      ?? items.filter((t) => t.precheck_intercepted || String(t.error || '').includes('PRECHECK_PHONE_ALREADY_REGISTERED')).length,
    noNumber: currentBatch.value?.no_number
      ?? items.filter((t) => t.no_number || String(t.error || '').includes('noNumber')).length
  }
})

const displayLogs = computed(() => {
  if (mergedLogView.value) {
    const ids = selectedTaskIds.value.length
      ? selectedTaskIds.value
      : (currentBatch.value?.task_ids || [])
    const rows = []
    for (const tid of ids) {
      const task = taskList.value.find((t) => t.task_id === tid)
      for (const line of (task?.logs || [])) {
        rows.push(`[${tid}] ${line}`)
      }
    }
    return rows
  }
  return activeTask.value?.logs || []
})

const scrollTerminal = () => {
  nextTick(() => {
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight
    }
  })
}

let lastPolledLogFingerprint = ''

export const fetchTasks = async () => {
  try {
    const params = new URLSearchParams()
    if (taskFilter.value === 'batch' && currentBatch.value?.batch_id) {
      params.set('batch_id', currentBatch.value.batch_id)
    }
    if (activeTask.value?.task_id) {
      params.set('active_task_id', activeTask.value.task_id)
    }
    const qs = params.toString() ? `?${params.toString()}` : ''
    const res = await fetch(`/api/register/tasks${qs}`)
    taskList.value = await res.json()
    if (currentBatch.value?.batch_id) {
      try {
        const bres = await fetch(`/api/register/batches/${currentBatch.value.batch_id}`)
        if (bres.ok) {
          currentBatch.value = await bres.json()
        }
      } catch (e) {
        console.error('Fetch batch error:', e)
      }
    }
    if (activeTask.value?.task_id) {
      const found = taskList.value.find((t) => t.task_id === activeTask.value.task_id)
      if (found) {
        activeTask.value = found
        const fp = `${found.task_id}:${(found.logs || []).length}:${found.status}:${found.updated_at || ''}`
        if (fp !== lastPolledLogFingerprint) {
          lastPolledLogFingerprint = fp
          scrollTerminal()
        }
      }
    }
  } catch (e) {
    console.error('Fetch tasks error:', e)
  }
}

export const fetchDbStats = async () => {
  try {
    const res = await fetch('/api/device-db-stats')
    dbStats.value = await res.json()
  } catch (e) {
    console.error('Fetch db stats error:', e)
  }
}

export const fetchProfiles = async () => {
  try {
    const res = await fetch('/api/device-profiles')
    deviceProfiles.value = await res.json()
  } catch (e) {
    console.error('Fetch profiles error:', e)
  }
}

export const fetchSessions = async () => {
  try {
    const res = await fetch('/api/sessions')
    sessions.value = await res.json()
  } catch (e) {
    console.error('Fetch sessions error:', e)
  }
}

export const fetchPhonePrecheckStatus = async () => {
  try {
    const res = await fetch('/api/phone-precheck/status')
    if (res.ok) {
      phonePrecheckStatus.value = await res.json()
    }
  } catch (e) {
    console.error('Fetch phone precheck status error:', e)
  }
}

export const startRegistrationTask = async () => {
  isStartingTask.value = true
  startError.value = ''
  const bootLogs = []
  try {
    if (config.use_proxy_seller_auto) {
      const preview = await previewAutoSelect(form.country, false)
      if (preview?.proxy) {
        matchedProxy.value = preview.proxy
        bootLogs.push(
          `[${new Date().toLocaleTimeString()}] [多径中继网关] 启动前已匹配 ${form.country.toUpperCase()} 区域代理: ${preview.proxy.proxy_type || 'socks5'}://${preview.proxy.addr}:${preview.proxy.port}`
        )
      } else if (preview?.message) {
        bootLogs.push(`[${new Date().toLocaleTimeString()}] [多径中继网关] ${preview.message}`)
      }
    }
    const useHunt = huntMode.value && Number(huntAttempts.value) > 1
    const useBatch = batchMode.value && Number(batchCount.value) > 1
    const endpoint = useBatch ? '/api/register/batch' : '/api/register/start'
    const payload = {
      country: form.country,
      app_type: form.app_type,
      proxy_mode: form.proxy_mode || 'custom_pool',
      sms_provider: form.sms_provider || config.sms_provider || 'fivesim'
    }
    const taskMaxPrice = Number(form.max_price)
    if (Number.isFinite(taskMaxPrice) && taskMaxPrice > 0) {
      payload.max_price = taskMaxPrice
    }
    if (form.proxy_mode === 'explicit' && form.proxy_id) {
      payload.proxy_id = form.proxy_id
    }
    if (useHunt) {
      payload.max_number_attempts = Math.max(2, Math.min(500, Number(huntAttempts.value) || 100))
      payload.no_number_retries = 20
    }
    if (useBatch) {
      payload.count = Number(batchCount.value)
      payload.concurrency = effectiveConcurrency.value
    }
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.detail || data.message || '任务提交失败')
    }
    if (useBatch) {
      currentBatch.value = data
      taskFilter.value = 'batch'
      selectedTaskIds.value = [...(data.task_ids || [])]
      mergedLogView.value = true
      const firstId = (data.task_ids || [])[0]
      activeTask.value = {
        task_id: firstId,
        status: 'pending',
        batch_id: data.batch_id,
        logs: [
          ...bootLogs,
          `[${new Date().toLocaleTimeString()}] 并发批次 ${data.batch_id} 已提交：${(data.task_ids || []).join(', ')} (concurrency=${data.concurrency})`
          + (useHunt
            ? ` · 每任务循环试号最多 ${payload.max_number_attempts} 次（各自复用本任务 Push）`
            : '')
        ]
      }
    } else {
      currentBatch.value = null
      mergedLogView.value = false
      activeTask.value = {
        task_id: data.task_id,
        status: 'pending',
        logs: [
          ...bootLogs,
          useHunt
            ? `[${new Date().toLocaleTimeString()}] 循环试号任务 ${data.task_id} 已提交（最多换号 ${payload.max_number_attempts} 次，复用 Push Token）...`
            : `[${new Date().toLocaleTimeString()}] 虚拟节点任务 ${data.task_id} 已提交至状态机编排引擎...`
        ]
      }
    }
    await fetchTasks()
  } catch (e) {
    startError.value = e.message
    pushToast('danger', `任务提交失败: ${e.message}`)
  } finally {
    isStartingTask.value = false
  }
}

export const applyIncomingBatch = (data) => {
  currentBatch.value = data
  taskFilter.value = 'batch'
  selectedTaskIds.value = [...(data.task_ids || [])]
  mergedLogView.value = true
  const firstId = (data.task_ids || [])[0]
  activeTask.value = {
    task_id: firstId,
    status: 'pending',
    batch_id: data.batch_id,
    logs: [
      `[${new Date().toLocaleTimeString()}] 并发批次 ${data.batch_id} 已提交：${(data.task_ids || []).join(', ')} (concurrency=${data.concurrency})`
    ]
  }
}

export const viewTaskLogs = (t) => {
  mergedLogView.value = false
  activeTask.value = t
  scrollTerminal()
}

export const toggleTaskSelection = (taskId) => {
  const set = new Set(selectedTaskIds.value)
  if (set.has(taskId)) set.delete(taskId)
  else set.add(taskId)
  selectedTaskIds.value = [...set]
}

export const toggleSelectVisibleTasks = () => {
  const ids = visibleTaskList.value.map((t) => t.task_id)
  if (allVisibleSelected.value) {
    selectedTaskIds.value = selectedTaskIds.value.filter((id) => !ids.includes(id))
    return
  }
  selectedTaskIds.value = [...new Set([...selectedTaskIds.value, ...ids])]
}

export const viewSelectedLogs = () => {
  if (!selectedTaskIds.value.length) return
  mergedLogView.value = true
  const first = taskList.value.find((t) => t.task_id === selectedTaskIds.value[0])
  if (first) activeTask.value = first
}

export const focusBatchTask = (taskId) => {
  const found = taskList.value.find((t) => t.task_id === taskId)
  mergedLogView.value = false
  if (found) {
    activeTask.value = found
    return
  }
  activeTask.value = { task_id: taskId, status: 'pending', logs: [] }
}

export const clearActiveLogs = () => {
  if (activeTask.value) activeTask.value.logs = []
}

export const retryTask = async (task) => {
  if (task?.batch_id && currentBatch.value?.country) {
    form.country = currentBatch.value.country
  }
  if (task?.batch_id && currentBatch.value?.app_type) {
    form.app_type = currentBatch.value.app_type
  }
  batchMode.value = false
  await startRegistrationTask()
}

export const openTaskDetail = async (task) => {
  try {
    const res = await fetch(`/api/register/tasks/${task.task_id}`)
    if (res.ok) {
      detailTask.value = await res.json()
      return
    }
  } catch (e) {
    console.error('Fetch task detail error:', e)
  }
  detailTask.value = task
}

export const useTasks = () => ({
  batchMode,
  batchCount,
  batchConcurrency,
  huntMode,
  huntAttempts,
  currentBatch,
  taskFilter,
  selectedTaskIds,
  mergedLogView,
  isStartingTask,
  startError,
  activeTask,
  taskList,
  sessions,
  deviceProfiles,
  dbStats,
  terminalRef,
  phonePrecheckStatus,
  effectiveConcurrency,
  visibleTaskList,
  allVisibleSelected,
  batchStats,
  displayLogs,
  fetchDbStats,
  fetchProfiles,
  fetchSessions,
  fetchPhonePrecheckStatus,
  fetchTasks,
  startRegistrationTask,
  applyIncomingBatch,
  viewTaskLogs,
  toggleTaskSelection,
  toggleSelectVisibleTasks,
  viewSelectedLogs,
  focusBatchTask,
  clearActiveLogs,
  retryTask,
  openTaskDetail
})
