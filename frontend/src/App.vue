<template>
  <div class="ce-app">
    <div v-if="!authReady" class="ce-boot">正在加载控制台…</div>
    <template v-else-if="!isAuthenticated">
      <LoginView />
      <ToastHost />
    </template>
    <template v-else>
      <AppHeader />
      <main class="ce-main">
        <ConsoleView v-if="activeTab === 'console'" />
        <VaultView v-else-if="activeTab === 'vault'" />
        <PushTokensView v-else-if="activeTab === 'tokens'" />
        <ProxyMeshView v-else-if="activeTab === 'proxy'" />
        <SettingsView v-else-if="activeTab === 'settings'" />
        <DevicesView v-else-if="activeTab === 'devices'" />
      </main>
      <ToastHost />
    </template>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, watch } from 'vue'
import AppHeader from './components/AppHeader.vue'
import ToastHost from './components/ToastHost.vue'
import LoginView from './components/LoginView.vue'
import ConsoleView from './components/console/ConsoleView.vue'
import VaultView from './components/vault/VaultView.vue'
import PushTokensView from './components/tokens/PushTokensView.vue'
import ProxyMeshView from './components/proxy/ProxyMeshView.vue'
import SettingsView from './components/settings/SettingsView.vue'
import DevicesView from './components/devices/DevicesView.vue'
import { useUi } from './composables/useUi'
import { fetchMe, isAuthenticated, authReady } from './composables/useAuth'
import { fetchConfig, fetchAvailableCountries } from './composables/useConfig'
import { fetchCustomProxyList, refreshProxyPool } from './composables/useProxy'
import {
  fetchDbStats, fetchPhonePrecheckStatus, fetchProfiles, fetchSessions, fetchTasks, taskList
} from './composables/useTasks'
import { fetchDeviceCatalog } from './composables/useDevices'
import { fetchVaultAccounts, stopAppsPoll } from './composables/useVault'

const { activeTab, fetchEngineHealth } = useUi()

let pollTimer = null
let pollInFlight = false
let consoleStarted = false

const ACTIVE_TASK_STATUSES = new Set(['pending', 'running', 'waiting_code', 'logging_in'])

const hasActiveTasks = () =>
  (taskList.value || []).some((t) => ACTIVE_TASK_STATUSES.has(t?.status))

const tickPolling = async () => {
  if (pollInFlight) return
  if (typeof document !== 'undefined' && document.hidden) return
  // 非控制台页且无活跃任务时降载：只偶尔探活引擎，不拉全量任务
  const onConsole = activeTab.value === 'console'
  if (!onConsole && !hasActiveTasks()) {
    await fetchEngineHealth()
    return
  }
  pollInFlight = true
  try {
    const jobs = [fetchTasks(), fetchEngineHealth()]
    if (onConsole) jobs.push(fetchPhonePrecheckStatus())
    await Promise.all(jobs)
  } finally {
    pollInFlight = false
  }
}

const schedulePolling = () => {
  if (pollTimer) clearInterval(pollTimer)
  const interval = (activeTab.value === 'console' || hasActiveTasks()) ? 2500 : 8000
  pollTimer = setInterval(tickPolling, interval)
}

const startConsole = () => {
  if (consoleStarted) return
  consoleStarted = true
  fetchConfig()
  fetchAvailableCountries({ toast: false }).catch(() => {})
  fetchProfiles()
  fetchDbStats()
  fetchDeviceCatalog()
  fetchTasks()
  fetchSessions()
  fetchVaultAccounts()
  fetchPhonePrecheckStatus()
  fetchEngineHealth()
  refreshProxyPool('', false)
  fetchCustomProxyList()
  schedulePolling()
}

const stopConsole = () => {
  consoleStarted = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  stopAppsPoll()
}

onMounted(() => {
  fetchMe()
  fetchEngineHealth()
  document.addEventListener('visibilitychange', onVisibility)
})

const onVisibility = () => {
  if (!isAuthenticated.value || document.hidden) return
  tickPolling()
}

watch(isAuthenticated, (ok) => {
  if (ok) startConsole()
  else stopConsole()
})

watch(activeTab, () => {
  if (!isAuthenticated.value) return
  schedulePolling()
  if (activeTab.value === 'console') tickPolling()
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisibility)
  stopConsole()
})
</script>
