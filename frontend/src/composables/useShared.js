export const PUBLISHED_API_IDS = new Set([4, 6, 8, 10, 2040, 2100, 17349, 21724])

/** 全球国家拓扑目录：国旗由 ISO-2 运行时合成，分组供控制台 / 参数 / 指纹库共用。 */
export const COUNTRY_CATALOG = [
  { value: 'ca', name_zh: '加拿大', name_en: 'Canada', dial: '+1', group: 'na' },
  { value: 'us', name_zh: '美国', name_en: 'USA', dial: '+1', group: 'na' },
  { value: 'mx', name_zh: '墨西哥', name_en: 'Mexico', dial: '+52', group: 'na' },
  { value: 'cl', name_zh: '智利', name_en: 'Chile', dial: '+56', group: 'sa', hint: '推荐基线拓扑' },
  { value: 'br', name_zh: '巴西', name_en: 'Brazil', dial: '+55', group: 'sa' },
  { value: 'co', name_zh: '哥伦比亚', name_en: 'Colombia', dial: '+57', group: 'sa' },
  { value: 'pe', name_zh: '秘鲁', name_en: 'Peru', dial: '+51', group: 'sa' },
  { value: 'ar', name_zh: '阿根廷', name_en: 'Argentina', dial: '+54', group: 'sa' },
  { value: 'gb', name_zh: '英国', name_en: 'United Kingdom', dial: '+44', group: 'eu' },
  { value: 'de', name_zh: '德国', name_en: 'Germany', dial: '+49', group: 'eu' },
  { value: 'fr', name_zh: '法国', name_en: 'France', dial: '+33', group: 'eu' },
  { value: 'ru', name_zh: '俄罗斯', name_en: 'Russia', dial: '+7', group: 'cis' },
  { value: 'ua', name_zh: '乌克兰', name_en: 'Ukraine', dial: '+380', group: 'cis' },
  { value: 'kz', name_zh: '哈萨克斯坦', name_en: 'Kazakhstan', dial: '+7', group: 'cis' },
  { value: 'uz', name_zh: '乌兹别克斯坦', name_en: 'Uzbekistan', dial: '+998', group: 'cis' },
  { value: 'tr', name_zh: '土耳其', name_en: 'Turkey', dial: '+90', group: 'me' },
  { value: 'ae', name_zh: '阿联酋', name_en: 'UAE', dial: '+971', group: 'me' },
  { value: 'sa', name_zh: '沙特', name_en: 'Saudi Arabia', dial: '+966', group: 'me' },
  { value: 'eg', name_zh: '埃及', name_en: 'Egypt', dial: '+20', group: 'me' },
  { value: 'af', name_zh: '阿富汗', name_en: 'Afghanistan', dial: '+93', group: 'me' },
  { value: 'za', name_zh: '南非', name_en: 'South Africa', dial: '+27', group: 'af' },
  { value: 'ng', name_zh: '尼日利亚', name_en: 'Nigeria', dial: '+234', group: 'af' },
  { value: 'ke', name_zh: '肯尼亚', name_en: 'Kenya', dial: '+254', group: 'af' },
  { value: 'in', name_zh: '印度', name_en: 'India', dial: '+91', group: 'apac', hint: '专属住宅池' },
  { value: 'id', name_zh: '印尼', name_en: 'Indonesia', dial: '+62', group: 'apac' },
  { value: 'jp', name_zh: '日本', name_en: 'Japan', dial: '+81', group: 'apac' },
  { value: 'kr', name_zh: '韩国', name_en: 'South Korea', dial: '+82', group: 'apac' },
  { value: 'th', name_zh: '泰国', name_en: 'Thailand', dial: '+66', group: 'apac' },
  { value: 'vn', name_zh: '越南', name_en: 'Vietnam', dial: '+84', group: 'apac' },
  { value: 'ph', name_zh: '菲律宾', name_en: 'Philippines', dial: '+63', group: 'apac' },
  { value: 'au', name_zh: '澳大利亚', name_en: 'Australia', dial: '+61', group: 'apac' }
]

export const COUNTRY_GROUP_META = [
  { id: 'na', label: '北美 · North America' },
  { id: 'sa', label: '南美 · South America' },
  { id: 'eu', label: '西欧 · Western Europe' },
  { id: 'cis', label: '东欧 / CIS · Eastern Europe' },
  { id: 'me', label: '中东 · Middle East' },
  { id: 'af', label: '非洲 · Africa' },
  { id: 'apac', label: '亚太 · Asia-Pacific' }
]

export const countryFlag = (code) => {
  const iso = String(code || '').trim().toUpperCase()
  if (iso.length !== 2 || !/^[A-Z]{2}$/.test(iso)) return '🏳️'
  return String.fromCodePoint(...[...iso].map((ch) => 127397 + ch.charCodeAt(0)))
}

export const formatCountryLabel = (item) => {
  const flag = countryFlag(item.value || item.code)
  const zh = item.name_zh || item.name || ''
  const en = item.name_en || item.name || ''
  const dial = item.dial ? `, ${item.dial}` : ''
  const hint = item.hint ? ` · ${item.hint}` : ''
  const title = zh && en && zh !== en ? `${zh} (${en}${dial})` : `${en || zh}${dial ? ` (${dial.replace(/^, /, '')})` : ''}`
  return `${flag} ${title}${hint}`
}

export const COUNTRY_GROUPS = COUNTRY_GROUP_META.map((group) => ({
  ...group,
  options: COUNTRY_CATALOG.filter((item) => item.group === group.id).map((item) => ({
    value: item.value,
    label: formatCountryLabel(item),
    ...item
  }))
}))

export const COUNTRY_OPTIONS = COUNTRY_GROUPS.flatMap((group) => group.options)

export const APP_TYPE_OPTIONS = [
  { value: 'telegram_android', label: '📱 MTProto Android 官方主版 api_id=6 (Play Store / SDK 33)' },
  { value: 'telegram_android_public', label: '📱 MTProto Android Public api_id=4 (早期官方 Android)' },
  { value: 'telegram_x', label: '⚡ MTProto TDLib (官方极速版 / AID: 47f7...)' },
  { value: 'telegram_9', label: '🕰️ MTProto Legacy (经典稳定版 SDK 32 / AID: 59e5...)' }
]

export const maskHash = (hash) => {
  if (!hash) return '未配置'
  const text = String(hash)
  if (text.length <= 10) return text
  return `${text.substring(0, 8)}...${text.substring(text.length - 4)}`
}

export const formatTime = (iso) => {
  if (!iso) return '-'
  if (iso.includes('T')) return iso.split('T')[1]?.substring(0, 8) || iso
  return String(iso).slice(-8)
}

export const formatDuration = (start, end) => {
  if (!start) return '-'
  const a = Date.parse(start)
  const b = end ? Date.parse(end) : Date.now()
  if (Number.isNaN(a) || Number.isNaN(b)) return '-'
  const sec = Math.max(0, Math.round((b - a) / 1000))
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  return `${min}m ${sec % 60}s`
}

export const latencyWidth = (ms) => {
  if (ms == null || Number.isNaN(Number(ms))) return 8
  return Math.max(8, Math.min(100, Math.round(100 - Number(ms) / 18)))
}

export const getStatusBadgeClass = (status) => {
  if (status === 'success') return 'ce-badge is-success'
  if (status === 'running' || status === 'logging_in') return 'ce-badge is-info is-pulse'
  if (status === 'waiting_code') return 'ce-badge is-info is-pulse'
  if (status === 'failed') return 'ce-badge is-danger'
  if (status === 'canceled') return 'ce-badge is-warn'
  if (status === 'filtered') return 'ce-badge is-warn'
  return 'ce-badge is-warn'
}

export const classifyLogLine = (log) => {
  const text = String(log || '')
  if (text.includes('🎉') || text.includes('成功')) return 'ce-log is-ok'
  if (text.includes('❌') || text.includes('失败') || text.includes('异常') || text.includes('noNumber')) return 'ce-log is-err'
  if (text.includes('预检拦截')) return 'ce-log is-warn'
  if (text.includes('[*]') || text.includes('探测') || text.includes('预检') || text.includes('Recaptcha')) return 'ce-log is-info'
  return 'ce-log is-plain'
}

export const parseApiError = async (res) => {
  let data = {}
  try {
    data = await res.json()
  } catch {
    data = {}
  }
  if (!res.ok) {
    throw new Error(data.detail || data.message || `请求失败 HTTP ${res.status}`)
  }
  return data
}

export const isCrossProviderUrl = (url, provider) => {
  const host = String(url || '').toLowerCase()
  if (provider === 'reghelp') return host.includes('antisafety.net')
  return host.includes('reghelp.net')
}

export const parseLines = (text) => String(text || '')
  .split('\n')
  .map((s) => s.trim())
  .filter(Boolean)
