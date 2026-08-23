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
  phone_precheck_enabled: true
})

const form = reactive({
  country: 'cl',
  app_type: 'telegram_android',
  proxy_mode: 'custom_pool',
  proxy_id: ''
})

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
    syncBaseUrlsTextFromConfig()
  } catch (e) {
    console.error('Fetch config error:', e)
    pushToast('danger', `读取全局配置失败: ${e.message}`)
  }
}

export const saveConfig = async () => {
  isSavingConfig.value = true
  applyBaseUrlsTextToConfig()
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
