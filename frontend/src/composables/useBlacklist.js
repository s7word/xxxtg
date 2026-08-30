import { reactive, ref } from 'vue'
import { pushToast } from './useUi'

const blacklistLoading = ref(false)
const blacklistBusy = ref('')
const blacklistQuery = ref('')
const blacklistCategory = ref('')
const blacklistCountry = ref('')
const blacklistSummary = reactive({
  total: 0,
  banned: 0,
  already_registered: 0,
  app_delivery_unusable: 0,
  manual: 0,
})
const blacklistItems = ref([])
const blacklistTotal = ref(0)
const blacklistMessage = ref('')
const addForm = reactive({
  phone: '',
  category: 'manual',
  note: '',
})

export const fetchBlacklist = async () => {
  blacklistLoading.value = true
  try {
    const params = new URLSearchParams()
    if (blacklistQuery.value.trim()) params.set('q', blacklistQuery.value.trim())
    if (blacklistCategory.value) params.set('category', blacklistCategory.value)
    if (blacklistCountry.value.trim()) params.set('country', blacklistCountry.value.trim().toLowerCase())
    params.set('limit', '300')
    const res = await fetch(`/api/banned-phones?${params.toString()}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '加载失败')
    Object.assign(blacklistSummary, data.summary || {})
    blacklistItems.value = data.items || []
    blacklistTotal.value = data.total || 0
    blacklistMessage.value = data.message || ''
  } catch (e) {
    pushToast('danger', `读取号码黑名单失败: ${e.message}`)
  } finally {
    blacklistLoading.value = false
  }
}

export const addBlacklistPhone = async () => {
  const phone = addForm.phone.trim()
  if (!phone) {
    pushToast('danger', '请填写号码')
    return
  }
  blacklistBusy.value = 'add'
  try {
    const res = await fetch('/api/banned-phones', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        phone,
        category: addForm.category || 'manual',
        reason: 'MANUAL_BLACKLIST',
        note: addForm.note || '',
      }),
    })
    const data = await res.json()
    if (!res.ok || !data.success) throw new Error(data.detail || data.message || '添加失败')
    pushToast('ok', data.message || '已收录')
    addForm.phone = ''
    addForm.note = ''
    await fetchBlacklist()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    blacklistBusy.value = ''
  }
}

export const deleteBlacklistPhone = async (phone) => {
  if (!phone) return
  blacklistBusy.value = phone
  try {
    const res = await fetch(`/api/banned-phones/${encodeURIComponent(phone)}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok || !data.success) throw new Error(data.message || data.detail || '删除失败')
    pushToast('ok', data.message || '已移除')
    await fetchBlacklist()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    blacklistBusy.value = ''
  }
}

export const purgeBlacklist = async (category = null) => {
  blacklistBusy.value = 'purge'
  try {
    const res = await fetch('/api/banned-phones/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: category || null }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '清理失败')
    pushToast('ok', data.message || `已清理 ${data.deleted || 0} 条`)
    await fetchBlacklist()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    blacklistBusy.value = ''
  }
}

export const useBlacklist = () => ({
  blacklistLoading,
  blacklistBusy,
  blacklistQuery,
  blacklistCategory,
  blacklistCountry,
  blacklistSummary,
  blacklistItems,
  blacklistTotal,
  blacklistMessage,
  addForm,
  fetchBlacklist,
  addBlacklistPhone,
  deleteBlacklistPhone,
  purgeBlacklist,
})
