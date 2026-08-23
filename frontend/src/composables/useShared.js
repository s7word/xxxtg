export const PUBLISHED_API_IDS = new Set([4, 6, 8, 10, 2040, 2100, 17349, 21724])

export const COUNTRY_OPTIONS = [
  { value: 'cl', label: '🇨🇱 智利 (Chile, +56) · 推荐基线拓扑' },
  { value: 'in', label: '🇮🇳 印度 (India, +91) · 专属住宅池' },
  { value: 'id', label: '🇮🇩 印尼 (Indonesia, +62)' },
  { value: 'af', label: '🇦🇫 阿富汗 (Afghanistan, +93)' },
  { value: 'kz', label: '🇰🇿 哈萨克斯坦 (Kazakhstan, +7)' },
  { value: 'ru', label: '🇷🇺 俄罗斯 (Russia, +7)' },
  { value: 'br', label: '🇧🇷 巴西 (Brazil, +55)' },
  { value: 'us', label: '🇺🇸 美国 (USA, +1)' }
]

export const APP_TYPE_OPTIONS = [
  { value: 'telegram_android', label: '📱 MTProto Android (官方主版 SDK 33 / AID: 308a...)' },
  { value: 'telegram_x', label: '⚡ MTProto TDLib (官方极速版 / AID: 47f7...)' },
  { value: 'telegram_9', label: '🕰️ MTProto Legacy (经典稳定版 SDK 32 / AID: 59e5...)' }
]

export const countryFlag = (code) => {
  const iso = String(code || '').trim().toUpperCase()
  if (iso.length !== 2 || !/^[A-Z]{2}$/.test(iso)) return '🏳️'
  return String.fromCodePoint(...[...iso].map((ch) => 127397 + ch.charCodeAt(0)))
}

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
  if (status === 'running') return 'ce-badge is-info is-pulse'
  if (status === 'failed') return 'ce-badge is-danger'
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
