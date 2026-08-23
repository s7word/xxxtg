import { ref } from 'vue'

export const tabs = [
  { id: 'console', name: '状态机引导控制台', en: 'Console & Execution', icon: 'zap' },
  { id: 'vault', name: '凭证库 & 开发者 API', en: 'Account Vault & Telegram Apps', icon: 'shield' },
  { id: 'proxy', name: '代理网关 & 多径中继', en: 'Proxy Relay & Custom Pool', icon: 'globe' },
  { id: 'settings', name: '参数拓扑 & 探针审计', en: 'Parameters & Audit Probes', icon: 'sliders' },
  { id: 'devices', name: '硬件指纹 & 拓扑库', en: 'Device Profiles & Base.db', icon: 'smartphone' }
]

const activeTab = ref('console')
const terminalExpanded = ref(false)
const detailTask = ref(null)
const toasts = ref([])
const engineHealth = ref({ status: 'pending', version: '2.2.0', message: '正在探测仿真引擎...' })

let toastSeq = 0
let toastTimers = new Map()

export const pushToast = (type, message) => {
  const id = ++toastSeq
  toasts.value = [...toasts.value.slice(-4), { id, type, message }]
  const timer = setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id)
    toastTimers.delete(id)
  }, 4200)
  toastTimers.set(id, timer)
}

export const dismissToast = (id) => {
  const timer = toastTimers.get(id)
  if (timer) clearTimeout(timer)
  toastTimers.delete(id)
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

export const goTab = (id) => {
  activeTab.value = id
  terminalExpanded.value = false
}

export const fetchEngineHealth = async () => {
  try {
    const res = await fetch('/api/health')
    const data = await res.json()
    engineHealth.value = {
      status: data.status === 'ok' ? 'ok' : 'off',
      version: data.version || '2.2.0',
      message: data.engine || '仿真审计引擎'
    }
  } catch (e) {
    engineHealth.value = { status: 'off', version: '2.2.0', message: e.message }
  }
}

export const useUi = () => ({
  tabs,
  activeTab,
  terminalExpanded,
  detailTask,
  toasts,
  engineHealth,
  pushToast,
  dismissToast,
  goTab,
  fetchEngineHealth
})
