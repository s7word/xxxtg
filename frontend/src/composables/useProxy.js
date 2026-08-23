import { computed, reactive, ref } from 'vue'
import { useConfig } from './useConfig'
import { pushToast } from './useUi'

const { config, form, saveConfig } = useConfig()

const proxyPool = ref([])
const proxyPoolMeta = reactive({
  success: null,
  message: '',
  available_countries: [],
  cached: false
})
const matchedProxy = ref(null)
const customProxies = ref([])
const customProxyText = ref('')
const customProxyImportProbe = ref(false)
const customProxyImportCountry = ref('')
const customProxyMeta = reactive({
  success: null,
  message: '',
  countries: []
})

const testing = reactive({
  proxyseller: false,
  proxypool: false,
  autoselect: false,
  proxyall: false,
  connectivity: false,
  customimport: false,
  customall: false,
  customclear: false
})

export const customProxiesForCountry = computed(() => {
  const wanted = String(form.country || config.target_country || '').trim().toLowerCase()
  if (!wanted) return customProxies.value
  return customProxies.value.filter((item) => {
    const code = String(item.country_code || '').toLowerCase()
    const name = String(item.country || '').toLowerCase()
    return code === wanted || name.includes(wanted)
  })
})

export const customProxySummaryText = computed(() => {
  const total = customProxies.value.length
  const healthy = customProxies.value.filter((item) => item.healthy === true).length
  const pending = customProxies.value.filter((item) => item.healthy == null).length
  if (!total) return '空'
  return `${total} 条 / ${healthy} 通 / ${pending} 待测`
})

export const applyCustomProxyPayload = (data) => {
  if (!data) return
  if (Array.isArray(data.proxies)) customProxies.value = data.proxies
  if (Array.isArray(data.results) && !data.proxies) customProxies.value = data.results
  config.custom_proxies = customProxies.value
  customProxyMeta.success = data.success
  customProxyMeta.message = data.message || ''
  customProxyMeta.countries = data.countries || []
  if (data.fallback_proxy) Object.assign(config.fallback_proxy, data.fallback_proxy)
}

export const fetchCustomProxyList = async (country) => {
  try {
    const params = new URLSearchParams()
    if (country) params.set('country', country)
    const res = await fetch(`/api/proxy/custom-list${params.toString() ? '?' + params.toString() : ''}`)
    const data = await res.json()
    applyCustomProxyPayload(data)
    return data
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
    return null
  }
}

export const refreshProxyPool = async (country, refresh = true) => {
  testing.proxypool = true
  try {
    const params = new URLSearchParams()
    if (country) params.set('country', country)
    if (refresh) params.set('refresh', 'true')
    const res = await fetch(`/api/proxy-seller/proxies?${params.toString()}`)
    const data = await res.json()
    proxyPool.value = data.proxies || []
    proxyPoolMeta.success = data.success
    proxyPoolMeta.message = data.message || ''
    proxyPoolMeta.available_countries = data.available_countries || []
    proxyPoolMeta.cached = !!data.cached
    const regional = (data.proxies || []).find((p) => {
      const code = String(p.country_code || p.country || '').toLowerCase()
      return !country || code.includes(String(country).toLowerCase())
    })
    if (regional) matchedProxy.value = regional
    return data
  } catch (e) {
    proxyPoolMeta.success = false
    proxyPoolMeta.message = e.message
    return null
  } finally {
    testing.proxypool = false
  }
}

export const previewAutoSelect = async (country, applyFallback = false) => {
  testing.autoselect = true
  try {
    const res = await fetch('/api/proxy-seller/auto-select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_country: country,
        apply_fallback: applyFallback,
        probe: false,
        allow_fallback: true,
        api_key: config.proxy_seller_key
      })
    })
    const data = await res.json()
    proxyPoolMeta.success = data.success
    proxyPoolMeta.message = data.message || ''
    if (data.proxy) matchedProxy.value = data.proxy
    if (data.fallback_proxy) Object.assign(config.fallback_proxy, data.fallback_proxy)
    return data
  } catch (e) {
    proxyPoolMeta.success = false
    proxyPoolMeta.message = e.message
    return { success: false, message: e.message }
  } finally {
    testing.autoselect = false
  }
}

export const setProxyAsFallback = async (proxy) => {
  config.fallback_proxy.proxy_type = proxy.proxy_type || 'socks5'
  config.fallback_proxy.addr = proxy.addr
  config.fallback_proxy.port = Number(proxy.port)
  config.fallback_proxy.username = proxy.username || ''
  config.fallback_proxy.password = proxy.password || ''
  matchedProxy.value = proxy
  await saveConfig()
}

export const importCustomProxyText = async () => {
  if (!customProxyText.value.trim()) {
    customProxyMeta.success = false
    customProxyMeta.message = '请先粘贴代理列表'
    return
  }
  testing.customimport = true
  try {
    const res = await fetch('/api/proxy/import-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: customProxyText.value,
        probe: customProxyImportProbe.value,
        replace: false,
        default_protocol: 'socks5',
        default_country: customProxyImportCountry.value || undefined
      })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    if (data.success) customProxyText.value = ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customimport = false
  }
}

export const testAllCustomProxies = async () => {
  testing.customall = true
  try {
    const res = await fetch('/api/proxy/test-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ concurrency: 4 })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customall = false
  }
}

export const setCustomProxyAsFallback = async (proxy) => {
  try {
    const res = await fetch('/api/proxy/set-fallback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        proxy_id: proxy.id,
        addr: proxy.addr,
        port: proxy.port,
        username: proxy.username
      })
    })
    const data = await res.json()
    applyCustomProxyPayload(data)
    if (data.success && data.fallback_proxy) {
      Object.assign(config.fallback_proxy, data.fallback_proxy)
      matchedProxy.value = data.proxy || proxy
    }
    if (!data.success) pushToast('danger', data.message || '设为后备失败')
  } catch (e) {
    pushToast('danger', `设为后备失败: ${e.message}`)
  }
}

export const deleteCustomProxy = async (proxy) => {
  if (!confirm(`删除自建代理 ${proxy.addr}:${proxy.port} ?`)) return
  try {
    const res = await fetch('/api/proxy/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy_id: proxy.id, addr: proxy.addr, port: proxy.port, username: proxy.username })
    })
    const data = await res.json()
    customProxyMeta.success = data.success
    customProxyMeta.message = data.message || ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  }
}

export const clearCustomProxyPool = async () => {
  if (!customProxies.value.length) return
  if (!confirm('确定清空全部自建代理？此操作会从配置中删除已导入列表。')) return
  testing.customclear = true
  try {
    const res = await fetch('/api/proxy/delete', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clear_all: true })
    })
    const data = await res.json()
    customProxyMeta.success = data.success
    customProxyMeta.message = data.message || ''
    await fetchCustomProxyList()
  } catch (e) {
    customProxyMeta.success = false
    customProxyMeta.message = e.message
  } finally {
    testing.customclear = false
  }
}

export const useProxy = () => ({
  proxyPool,
  proxyPoolMeta,
  matchedProxy,
  customProxies,
  customProxyText,
  customProxyImportProbe,
  customProxyImportCountry,
  customProxyMeta,
  customProxiesForCountry,
  customProxySummaryText,
  testing,
  applyCustomProxyPayload,
  fetchCustomProxyList,
  refreshProxyPool,
  previewAutoSelect,
  setProxyAsFallback,
  importCustomProxyText,
  testAllCustomProxies,
  setCustomProxyAsFallback,
  deleteCustomProxy,
  clearCustomProxyPool
})
