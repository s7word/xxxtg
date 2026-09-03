import { reactive, ref } from 'vue'
import { pushToast } from './useUi'

const pushTokenLoading = ref(false)
const pushTokenBusy = ref('')
const pushTokenMeta = reactive({
  reuse_enabled: false,
  reuse_max_uses: 2,
  save_issued: true,
})
const pushTokenSummary = reactive({
  total: 0,
  available: 0,
  unused: 0,
  used_once: 0,
  reusable: 0,
  consumed: 0,
  refunded: 0,
})
const pushTokenItems = ref([])

export const fetchPushTokens = async () => {
  pushTokenLoading.value = true
  try {
    const res = await fetch('/api/push-tokens')
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '加载失败')
    Object.assign(pushTokenSummary, data.summary || {})
    pushTokenItems.value = data.items || []
    pushTokenMeta.reuse_enabled = !!data.reuse_enabled
    pushTokenMeta.reuse_max_uses = data.reuse_max_uses ?? 2
    pushTokenMeta.save_issued = data.save_issued !== false
  } catch (e) {
    pushToast('danger', `读取 Push Token 库存失败: ${e.message}`)
  } finally {
    pushTokenLoading.value = false
  }
}

export const deletePushToken = async (id) => {
  if (!id) return
  pushTokenBusy.value = id
  try {
    const res = await fetch(`/api/push-tokens/${encodeURIComponent(id)}`, { method: 'DELETE' })
    const data = await res.json()
    if (!res.ok || !data.success) throw new Error(data.message || data.detail || '删除失败')
    pushToast('ok', data.message || '已删除')
    await fetchPushTokens()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    pushTokenBusy.value = ''
  }
}

export const purgePushTokens = async (opts = {}) => {
  pushTokenBusy.value = 'purge'
  try {
    const res = await fetch('/api/push-tokens/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        refunded: opts.refunded !== false,
        consumed: opts.consumed !== false,
        exhausted: !!opts.exhausted,
      }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '清理失败')
    pushToast('ok', data.message || `已清理 ${data.deleted || 0} 条`)
    await fetchPushTokens()
  } catch (e) {
    pushToast('danger', e.message)
  } finally {
    pushTokenBusy.value = ''
  }
}

export const usePushTokens = () => ({
  pushTokenLoading,
  pushTokenBusy,
  pushTokenMeta,
  pushTokenSummary,
  pushTokenItems,
  fetchPushTokens,
  deletePushToken,
  purgePushTokens,
})
