import { computed, reactive, ref } from 'vue'
import { isCrossProviderUrl, parseLines, PUBLISHED_API_IDS } from './useShared'
import { pushToast } from './useUi'

const config = reactive({
  active_app_type: 'telegram_android',
  antisafety_api_key: '',
  antisafety_aids: {
    telegram_android: '',
    telegram_x: '',
    telegram_9: ''
  },
  vak_sms_api_key: '',
  sms_provider: 'fivesim',
  fivesim_api_key: '',
  grizzly_sms_api_key: '',
  smsbower_api_key: '',
  sms_max_price: null,
  smsall_webhook_secret: '',
  smsall_auto_register: false,
  smsall_auto_max_price_usd: 0.5,
  smsall_auto_count: 3,
  smsall_auto_concurrency: 3,
  smsall_auto_cooldown_seconds: 600,
  smsall_auto_min_stock: 1,
  smsall_auto_max_countries: 2,
  smsall_sniper_enabled: true,
  smsall_sniper_count: 10,
  smsall_sniper_concurrency: 10,
  smsall_sniper_max_number_attempts: 20,
  smsall_sniper_cooldown_seconds: 60,
  smsall_sniper_max_countries: 3,
  smsall_sniper_max_price_usd: null,
  smsall_sniper_price_caps: [],
  smsall_sniper_use_item_price_as_max: true,
  target_country: 'cl',
  proxy_seller_key: '',
  use_proxy_seller_auto: false,
  fallback_proxy: {
    proxy_type: 'socks5',
    addr: '127.0.0.1',
    port: 10808,
    username: '',
    password: ''
  },
  custom_proxies: [],
  default_2fa_password: 'Password@2026!Sec',
  api_credential_mode: 'auto',
  custom_api_id: null,
  custom_api_hash: '',
  antisafety_base_urls: ['https://api.antisafety.net'],
  antisafety_reporting_base_urls: ['https://reporting.antisafety.net'],
  antisafety_connect_timeout: 6.0,
  antisafety_total_timeout: 20.0,
  antisafety_enabled: true,
  reghelp_api_key: '',
  reghelp_base_urls: ['https://api.reghelp.net'],
  reghelp_enabled: true,
  reghelp_connect_timeout: 6.0,
  reghelp_total_timeout: 20.0,
  attestation_provider_mode: 'reghelp_primary',
  email_provider_mode: 'reghelp_primary',
  email_smsbower_fallback_enabled: true,
  push_token_reuse_enabled: false,
  push_token_reuse_max_uses: 2,
  push_token_save_issued: true,
  code_delivery_mode: 'balanced',
  official_client_emulation: false,
  hunt_sms_first_after_app_streak: 2,
  hunt_no_number_retries: 20,
  hunt_no_number_retry_delay_sec: 2.0,
  hunt_proxy_max_uses: 5,
  hunt_device_max_uses: 8,
  hunt_default_max_attempts: 100,
  hunt_max_total_leases: 200,
  phone_precheck_enabled: true
})

const form = reactive({
  country: 'cl',
  app_type: 'telegram_android',
  proxy_mode: 'custom_pool',
  proxy_id: '',
  sms_provider: 'fivesim',
  max_price: null,
  provider_ids: ''
})

const smsStock = reactive({
  items: [],
  total_countries: 0,
  total_stock: 0,
  updated_at: 0,
  provider: 'fivesim',
  cached: false,
  cache_age_seconds: 0,
  message: '',
  loading: false,
  error: ''
})
const countrySearch = ref('')

export const smsProviderLabel = (provider) => {
  const token = String(provider || '').toLowerCase().replace(/[-_]/g, '')
  if (token === 'vaksms') return 'Vak-SMS'
  if (token === 'grizzlysms') return 'Grizzly SMS'
  if (token === 'smsbower' || token === 'smsbowerapp' || token === 'bower') return 'SMS Bower'
  return '5SIM'
}

export const formatStockCount = (n) => {
  const value = Number(n) || 0
  if (value >= 10000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`
  return String(value)
}

export const formatStockOption = (item) => {
  if (!item) return ''
  const flag = item.flag || ''
  const zh = item.name_zh || item.name || ''
  const en = item.name || ''
  const title = zh && en && zh !== en ? `${zh} (${en})` : (zh || en || String(item.code || '').toUpperCase())
  const dial = item.dial ? ` (+${String(item.dial).replace(/^\+/, '')})` : ''
  const stock = formatStockCount(item.stock)
  const cost = item.cost != null && Number(item.cost) > 0 ? ` · ${Number(item.cost).toFixed(2)}₽` : ''
  return `${flag} ${title}${dial} · ⚡ ${stock} 货${cost}`
}

export const filteredStockCountries = computed(() => {
  const q = String(countrySearch.value || '').trim().toLowerCase()
  const items = smsStock.items || []
  if (!q) return items
  return items.filter((item) => {
    const hay = [
      item.code, item.name, item.name_zh, item.dial,
      item.dial ? `+${item.dial}` : '', item.provider_country_id
    ].join(' ').toLowerCase()
    return hay.includes(q)
  })
})

export const fetchAvailableCountries = async (opts = {}) => {
  const provider = opts.provider || form.sms_provider || config.sms_provider || 'fivesim'
  const refresh = !!opts.refresh
  smsStock.loading = true
  smsStock.error = ''
  try {
    const qs = new URLSearchParams({ provider })
    if (refresh) qs.set('refresh', 'true')
    const res = await fetch(`/api/sms/available-countries?${qs.toString()}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || data.message || '库存发现失败')
    smsStock.items = data.items || []
    smsStock.total_countries = data.total_countries || 0
    smsStock.total_stock = data.total_stock || 0
    smsStock.updated_at = data.updated_at || 0
    smsStock.provider = data.provider || provider
    smsStock.cached = !!data.cached
    smsStock.cache_age_seconds = data.cache_age_seconds || 0
    smsStock.message = data.message || ''
    const codes = new Set(smsStock.items.map((item) => String(item.code || '').toLowerCase()))
    if (smsStock.items.length) {
      const current = String(form.country || '').toLowerCase()
      if (!codes.has(current)) {
        form.country = smsStock.items[0].code
      }
      const cfgCurrent = String(config.target_country || '').toLowerCase()
      if (!codes.has(cfgCurrent)) {
        config.target_country = smsStock.items[0].code
      }
    }
    return data
  } catch (e) {
    smsStock.error = e.message
    console.error('Fetch available countries error:', e)
    if (opts.toast !== false) {
      pushToast('danger', `刷新实时有货国家失败: ${e.message}`)
    }
    throw e
  } finally {
    smsStock.loading = false
  }
}

const antisafetyBaseUrlsText = ref('')
const antisafetyReportingBaseUrlsText = ref('')
const reghelpBaseUrlsText = ref('')
const isSavingConfig = ref(false)

export const isPublishedCustomApiId = computed(() => PUBLISHED_API_IDS.has(Number(config.custom_api_id)))

export const syncBaseUrlsTextFromConfig = () => {
  antisafetyBaseUrlsText.value = (config.antisafety_base_urls || []).join('\n')
  antisafetyReportingBaseUrlsText.value = (config.antisafety_reporting_base_urls || []).join('\n')
  reghelpBaseUrlsText.value = (config.reghelp_base_urls || []).join('\n')
}

export const applyBaseUrlsTextToConfig = () => {
  config.antisafety_base_urls = parseLines(antisafetyBaseUrlsText.value)
    .filter((url) => !isCrossProviderUrl(url, 'antisafety'))
  config.antisafety_reporting_base_urls = parseLines(antisafetyReportingBaseUrlsText.value)
    .filter((url) => !isCrossProviderUrl(url, 'antisafety'))
  config.reghelp_base_urls = parseLines(reghelpBaseUrlsText.value)
    .filter((url) => !isCrossProviderUrl(url, 'reghelp'))
  if (!config.antisafety_base_urls.length) config.antisafety_base_urls = ['https://api.antisafety.net']
  if (!config.antisafety_reporting_base_urls.length) config.antisafety_reporting_base_urls = ['https://reporting.antisafety.net']
  if (!config.reghelp_base_urls.length) config.reghelp_base_urls = ['https://api.reghelp.net']
}

export const fetchConfig = async () => {
  try {
    const res = await fetch('/api/config')
    const data = await res.json()
    Object.assign(config, data)
    form.country = data.target_country || 'cl'
    form.app_type = data.active_app_type || 'telegram_android'
    form.sms_provider = data.sms_provider || 'fivesim'
    form.max_price = data.sms_max_price != null ? data.sms_max_price : null
    syncBaseUrlsTextFromConfig()
    fetchAvailableCountries({ provider: form.sms_provider, toast: false }).catch(() => {})
  } catch (e) {
    console.error('Fetch config error:', e)
    pushToast('danger', `读取全局配置失败: ${e.message}`)
  }
}

export const saveConfig = async () => {
  isSavingConfig.value = true
  applyBaseUrlsTextToConfig()
  const bid = Number(config.sms_max_price)
  config.sms_max_price = Number.isFinite(bid) && bid > 0 ? bid : null
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })
    const updated = await res.json()
    if (!res.ok) throw new Error(updated.detail || updated.message || '保存失败')
    Object.assign(config, updated)
    syncBaseUrlsTextFromConfig()
    pushToast('ok', '全局仿真参数已成功保存并持久化')
    return updated
  } catch (e) {
    pushToast('danger', `保存失败: ${e.message}`)
    throw e
  } finally {
    isSavingConfig.value = false
  }
}

export const useConfig = () => ({
  config,
  form,
  smsStock,
  countrySearch,
  filteredStockCountries,
  smsProviderLabel,
  formatStockCount,
  formatStockOption,
  fetchAvailableCountries,
  antisafetyBaseUrlsText,
  antisafetyReportingBaseUrlsText,
  reghelpBaseUrlsText,
  isSavingConfig,
  isPublishedCustomApiId,
  syncBaseUrlsTextFromConfig,
  applyBaseUrlsTextToConfig,
  fetchConfig,
  saveConfig
})
