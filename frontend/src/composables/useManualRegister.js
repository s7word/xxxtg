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

export const startManualRegistration = async () => {
  const { form } = useConfig()
  isSendingCode.value = true
  manualError.value = ''
  manualSuccess.value = null
  try {
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
    const res = await fetch('/api/register/manual/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: manualSession.value.task_id })
    })
    const data = await parseApiError(res)
    manualSession.value = { ...manualSession.value, ...data, status: data.status || 'canceled' }
    syncActiveTask(data)
    await fetchTasks()
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
  if (event.key === 'Enter' && !isSubmittingCode.value && String(manualCode.value || '').trim()) {
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
  resetManualPanel,
  goVaultFromManual,
  onManualCodeKeydown
})
