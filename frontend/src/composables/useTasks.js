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
// huntAttempts 只在用户手动点过档位后才生效，否则一律跟随全局 config.hunt_default_max_attempts，
// 免得前端硬编码把设置页里的全局参数永久顶掉
const huntAttempts = ref(null)
const huntAttemptsTouched = ref(false)
const currentBatch = ref(null)
const taskFilter = ref('all')
const selectedTaskIds = ref([])
const mergedLogView = ref(false)
const isStartingTask = ref(false)
const startError = ref('')
const cancelingTaskIds = ref(new Set())
const isCancelingBatch = ref(false)
const huntBudget = ref(null)
const isLoadingHuntBudget = ref(false)
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

const HUNT_ATTEMPTS_FALLBACK = 100

const effectiveHuntAttempts = computed(() => {
  if (huntAttemptsTouched.value) {
    const picked = Number(huntAttempts.value)
    if (Number.isFinite(picked) && picked > 0) return Math.min(500, Math.round(picked))
  }
  const fromConfig = Number(config.hunt_default_max_attempts)
  if (Number.isFinite(fromConfig) && fromConfig > 0) return Math.min(500, Math.round(fromConfig))
  return HUNT_ATTEMPTS_FALLBACK
})

export const setHuntAttempts = (value) => {
  huntAttemptsTouched.value = true
  huntAttempts.value = Number(value)
}

export const resetHuntAttemptsToConfig = () => {
  huntAttemptsTouched.value = false
  huntAttempts.value = null
}

const huntLeaseLimit = computed(() => {
  const limit = Number(config.hunt_max_total_leases)
  return Number.isFinite(limit) && limit > 0 ? limit : 200
})

/** 预计最多租号次数 = 任务数 × 每任务取号次数；后端会按同一上限裁剪。 */
const huntPlan = computed(() => {
  const count = batchMode.value ? Math.max(1, Number(batchCount.value) || 1) : 1
  const requested = huntMode.value ? effectiveHuntAttempts.value : 1
  const limit = huntLeaseLimit.value
  const attempts = count * requested > limit ? Math.max(1, Math.floor(limit / count)) : requested
  return {
    count,
    requested,
    attempts,
    limit,
    plannedLeases: count * attempts,
    clamped: attempts !== requested
  }
})

/** 代理是否被 1:1 钉死：批量槽位或显式指定出口时猎号不会轮换代理。 */
const huntProxyPinned = computed(
  () => batchMode.value || form.proxy_mode === 'explicit' || form.proxy_mode === 'fallback'
)

const huntProxyNote = computed(() => {
  if (batchMode.value) return '批量模式下每路任务钉死一个代理槽位，猎号期间不轮换出口，只轮换设备指纹与 Push'
  if (form.proxy_mode === 'explicit') return '已显式指定出口，猎号期间不轮换代理，只轮换设备指纹与 Push'
  if (form.proxy_mode === 'fallback') return '使用全局后备出口，池内无其它候选时不会轮换代理'
  const uses = Number(config.hunt_proxy_max_uses) || 5
  return `每 ${uses} 次 sendCode 尝试从注册代理池换一个同国节点；池里没有其它候选时会如实记日志并继续`
})

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
    const useHunt = huntMode.value && effectiveHuntAttempts.value > 1
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
      payload.max_number_attempts = Math.max(2, Math.min(500, effectiveHuntAttempts.value))
      // 不传 no_number_retries：让后端用全局 hunt_no_number_retries，
      // 否则前端硬编码的 20 会让设置页里的全局配置永远失效
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
    const effectiveAttempts = Number(data.max_number_attempts) || payload.max_number_attempts
    const clampNote = data.attempts_clamped
      ? `每任务取号次数已被联合上限裁剪 ${data.requested_max_number_attempts} → ${effectiveAttempts}`
      : ''
    if (clampNote) {
      bootLogs.push(`[${new Date().toLocaleTimeString()}] ⚠️ [猎号] ${clampNote}（上限 ${data.hunt_max_total_leases}）`)
      pushToast('warn', clampNote)
    }
    const huntNote = useHunt
      ? ` · 每任务最多试 ${effectiveAttempts} 个号，成功即停；预计最多租号 ${data.planned_leases ?? '?'} 次`
      : ''
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
          + huntNote
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
            ? `[${new Date().toLocaleTimeString()}] 猎号任务 ${data.task_id} 已提交（最多试 ${effectiveAttempts} 个号 · 成功即停 · 不可用号拉黑，同任务内优先复用 Push Token）...`
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

const CANCELABLE_STATUSES = ['pending', 'running', 'waiting_code', 'logging_in']

export const isCancelableTask = (task) =>
  CANCELABLE_STATUSES.includes(String(task?.status || 'pending'))

export const isCancelingAutoTask = (taskId) => cancelingTaskIds.value.has(taskId)

/**
 * 停止自动 / 猎号任务。后端只置取消标记，猎号循环在下一轮取号前收住，
 * 所以点完按钮后状态可能仍是 running：这是设计如此，不是没生效。
 */
export const cancelTask = async (taskId) => {
  if (!taskId) return null
  const next = new Set(cancelingTaskIds.value)
  next.add(taskId)
  cancelingTaskIds.value = next
  try {
    const res = await fetch(`/api/register/tasks/${taskId}/cancel`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '取消失败')
    pushToast(data.accepted ? 'ok' : 'warn', data.message || `任务 ${taskId} 取消已受理`)
    await fetchTasks()
    return data
  } catch (e) {
    pushToast('danger', `停止任务 ${taskId} 失败: ${e.message}`)
    throw e
  } finally {
    const done = new Set(cancelingTaskIds.value)
    done.delete(taskId)
    cancelingTaskIds.value = done
  }
}

export const cancelBatch = async (batchId) => {
  const target = batchId || currentBatch.value?.batch_id
  if (!target) return null
  isCancelingBatch.value = true
  try {
    const res = await fetch(`/api/register/batches/${target}/cancel`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '取消失败')
    pushToast('ok', data.message || `批次 ${target} 取消已受理`)
    await fetchTasks()
    return data
  } catch (e) {
    pushToast('danger', `停止批次 ${target} 失败: ${e.message}`)
    throw e
  } finally {
    isCancelingBatch.value = false
  }
}

/** 猎号启动前的租号预算与余额提示（后端会顺带查接码平台余额）。 */
export const refreshHuntBudget = async () => {
  isLoadingHuntBudget.value = true
  try {
    const params = new URLSearchParams({
      count: String(huntPlan.value.count),
      max_number_attempts: String(huntPlan.value.requested),
      check_balance: 'true'
    })
    if (form.sms_provider) params.set('sms_provider', form.sms_provider)
    const taskBid = Number(form.max_price)
    if (Number.isFinite(taskBid) && taskBid > 0) params.set('max_price', String(taskBid))
    const res = await fetch(`/api/register/hunt-budget?${params.toString()}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || '预算查询失败')
    huntBudget.value = data
    return data
  } catch (e) {
    huntBudget.value = null
    pushToast('warn', `租号预算查询失败: ${e.message}`)
    return null
  } finally {
    isLoadingHuntBudget.value = false
  }
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
  effectiveHuntAttempts,
  setHuntAttempts,
  resetHuntAttemptsToConfig,
  huntPlan,
  huntProxyPinned,
  huntProxyNote,
  huntBudget,
  isLoadingHuntBudget,
  refreshHuntBudget,
  currentBatch,
  taskFilter,
  selectedTaskIds,
  mergedLogView,
  isStartingTask,
  startError,
  isCancelingBatch,
  isCancelableTask,
  isCancelingAutoTask,
  cancelTask,
  cancelBatch,
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
