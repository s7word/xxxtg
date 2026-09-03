import { computed, ref } from 'vue'
import { useConfig } from './useConfig'
import { fetchSessions, fetchTasks, activeTask } from './useTasks'
import { goTab, pushToast } from './useUi'
import { parseApiError } from './useShared'

const launchMode = ref('auto')
const manualPhone = ref('')
const manualCountry = ref('')
const manualCode = ref('')
const manualPassword = ref('')
const isSendingCode = ref(false)
const isSubmittingCode = ref(false)
const isCancelingManual = ref(false)
const manualError = ref('')
const manualSession = ref(null)
const manualSuccess = ref(null)
const cancelingTaskIds = ref(new Set())

const ACTIVE_MANUAL_STATUSES = ['waiting_code', 'logging_in']

const isManualWaiting = computed(() =>
  manualSession.value?.status === 'waiting_code' || manualSession.value?.status === 'logging_in'
)

const deliveryBadgeClass = computed(() => {
  const type = String(manualSession.value?.delivery_type || '')
  if (type.includes('Sms')) return 'ce-badge is-success'
  if (type.includes('App')) return 'ce-badge is-warn'
  return 'ce-badge is-info'
})

const syncActiveTask = (data, extraLogs = []) => {
  if (!data?.task_id) return
  activeTask.value = {
    task_id: data.task_id,
    status: data.status,
    phone: data.phone,
    user_id: data.user_id,
    mode: 'manual',
    delivery_type: data.delivery_type,
    phone_code_hash: data.phone_code_hash,
    session_file: data.session_file,
    logs: data.logs?.length ? data.logs : extraLogs
  }
}

const setCanceling = (taskId, on) => {
  const next = new Set(cancelingTaskIds.value)
  if (on) next.add(taskId)
  else next.delete(taskId)
  cancelingTaskIds.value = next
}

export const isCancelingTaskId = (taskId) => cancelingTaskIds.value.has(taskId)

/**
 * 按 task_id 直接取消手动任务（与 manualSession 解耦）。
 * 用于任务队列表格里的「取消」按钮：即使页面刷新丢失了 composable 中的
 * manualSession（例如刷新页面），依然能通过任务列表拿到的 task_id 结束
 * 停留在 waiting_code 的手动任务。
 */
export const cancelManualTaskById = async (taskId, { silent = false } = {}) => {
  if (!taskId) return null
  setCanceling(taskId, true)
  try {
    const res = await fetch('/api/register/manual/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId })
    })
    const data = await parseApiError(res)
    if (manualSession.value?.task_id === taskId) {
      manualSession.value = { ...manualSession.value, ...data, status: data.status || 'canceled' }
    }
    if (activeTask.value?.task_id === taskId) {
      syncActiveTask(data)
    }
    await fetchTasks()
    if (!silent) pushToast('ok', `任务 ${taskId} 已取消`)
    return data
  } catch (e) {
    if (!silent) pushToast('danger', `取消任务 ${taskId} 失败: ${e.message}`)
    throw e
  } finally {
    setCanceling(taskId, false)
  }
}

export const startManualRegistration = async () => {
  if (isSendingCode.value) return
  const { form } = useConfig()
  isSendingCode.value = true
  manualError.value = ''
  manualSuccess.value = null
  try {
    // 同一手动会话再次点击「发送验证码」时，先取消仍处于 waiting_code/logging_in
    // 的旧任务，避免在任务队列里堆出多个僵尸 waiting_code 任务（同号任务应视为一个）。
    // 后端 start() 也会按号码做同样的去重兜底，这里是双重保险，覆盖页面未刷新的场景。
    if (
      manualSession.value?.task_id &&
      ACTIVE_MANUAL_STATUSES.includes(manualSession.value.status)
    ) {
      pushToast('warn', '检测到当前号码存在未完成的手动任务，自动取消旧任务后重新发码...')
      try {
        await cancelManualTaskById(manualSession.value.task_id, { silent: true })
      } catch (e) {
        // 取消失败也继续尝试新发码；后端仍会按号码去重，不会堆出多个任务
      }
      manualSession.value = null
    }
    const payload = {
      phone: String(manualPhone.value || '').trim(),
      app_type: form.app_type,
      proxy_mode: form.proxy_mode || 'custom_pool'
    }
    if (manualCountry.value) payload.country = manualCountry.value
    if (form.proxy_mode === 'explicit' && form.proxy_id) {
      payload.proxy_id = form.proxy_id
    }
    const res = await fetch('/api/register/manual/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await parseApiError(res)
    manualSession.value = data
    syncActiveTask(data)
    await fetchTasks()
    if (data.status === 'waiting_code') {
      pushToast('ok', `验证码已发送到 ${data.phone}（${data.delivery_type || '未知通道'}）`)
    } else if (data.status === 'failed') {
      manualError.value = data.message || data.error || '发码失败'
      pushToast('danger', manualError.value)
    }
    return data
  } catch (e) {
    manualError.value = e.message
    pushToast('danger', `手动发码失败: ${e.message}`)
    throw e
  } finally {
    isSendingCode.value = false
  }
}

export const submitManualCode = async () => {
  if (!manualSession.value?.task_id) {
    manualError.value = '没有等待中的手动任务，请先发送验证码'
    return
  }
  isSubmittingCode.value = true
  manualError.value = ''
  try {
    const payload = {
      task_id: manualSession.value.task_id,
      code: String(manualCode.value || '').trim()
    }
    if (manualPassword.value) payload.password = String(manualPassword.value).trim()
    const res = await fetch('/api/register/manual/submit-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await parseApiError(res)
    manualSession.value = { ...manualSession.value, ...data }
    syncActiveTask(data)
    await fetchTasks()
    if (data.status === 'success') {
      manualSuccess.value = data
      manualCode.value = ''
      pushToast('ok', `注册成功 UID ${data.user_id} · ${data.session_file || ''}`)
      await fetchSessions()
    } else if (data.status === 'waiting_code') {
      manualError.value = data.message || data.error || '验证码不正确，请重试'
      pushToast('danger', manualError.value)
    } else {
      manualError.value = data.message || data.error || '提交失败'
      pushToast('danger', manualError.value)
    }
    return data
  } catch (e) {
    manualError.value = e.message
    pushToast('danger', `提交验证码失败: ${e.message}`)
    throw e
  } finally {
    isSubmittingCode.value = false
  }
}

export const cancelManualTask = async () => {
  if (!manualSession.value?.task_id) return
  isCancelingManual.value = true
  manualError.value = ''
  try {
    const data = await cancelManualTaskById(manualSession.value.task_id, { silent: true })
    pushToast('ok', '手动任务已取消')
    return data
  } catch (e) {
    manualError.value = e.message
    pushToast('danger', `取消失败: ${e.message}`)
    throw e
  } finally {
    isCancelingManual.value = false
  }
}

export const resetManualPanel = () => {
  manualCode.value = ''
  manualPassword.value = ''
  manualError.value = ''
  manualSession.value = null
  manualSuccess.value = null
}

export const goVaultFromManual = () => {
  goTab('vault')
}

export const onManualCodeKeydown = (event) => {
  if (
    event.key === 'Enter' &&
    !isSubmittingCode.value &&
    !isCancelingManual.value &&
    String(manualCode.value || '').trim()
  ) {
    event.preventDefault()
    submitManualCode()
  }
}

export const useManualRegister = () => ({
  launchMode,
  manualPhone,
  manualCountry,
  manualCode,
  manualPassword,
  isSendingCode,
  isSubmittingCode,
  isCancelingManual,
  manualError,
  manualSession,
  manualSuccess,
  isManualWaiting,
  deliveryBadgeClass,
  startManualRegistration,
  submitManualCode,
  cancelManualTask,
  cancelManualTaskById,
  isCancelingTaskId,
  resetManualPanel,
  goVaultFromManual,
  onManualCodeKeydown
})
