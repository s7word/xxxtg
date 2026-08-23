<template>
  <div class="ce-app">
    <AppHeader />
    <main class="ce-main">
      <ConsoleView v-if="activeTab === 'console'" />
      <VaultView v-else-if="activeTab === 'vault'" />
      <ProxyMeshView v-else-if="activeTab === 'proxy'" />
      <SettingsView v-else-if="activeTab === 'settings'" />
      <DevicesView v-else-if="activeTab === 'devices'" />
    </main>
    <ToastHost />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue'
import AppHeader from './components/AppHeader.vue'
import ToastHost from './components/ToastHost.vue'
import ConsoleView from './components/console/ConsoleView.vue'
import VaultView from './components/vault/VaultView.vue'
import ProxyMeshView from './components/proxy/ProxyMeshView.vue'
import SettingsView from './components/settings/SettingsView.vue'
import DevicesView from './components/devices/DevicesView.vue'
import { useUi } from './composables/useUi'
import { fetchConfig } from './composables/useConfig'
import { fetchCustomProxyList, refreshProxyPool } from './composables/useProxy'
import {
  fetchDbStats, fetchPhonePrecheckStatus, fetchProfiles, fetchSessions, fetchTasks
} from './composables/useTasks'
import { fetchVaultAccounts, stopAppsPoll } from './composables/useVault'

const { activeTab, fetchEngineHealth } = useUi()

let pollTimer = null
let pollInFlight = false

const tickPolling = async () => {
  if (pollInFlight) return
  pollInFlight = true
  try {
    await Promise.all([
      fetchTasks(),
      fetchPhonePrecheckStatus(),
      fetchEngineHealth()
    ])
  } finally {
    pollInFlight = false
  }
}

onMounted(() => {
  fetchConfig()
  fetchProfiles()
  fetchDbStats()
  fetchTasks()
  fetchSessions()
  fetchVaultAccounts()
  fetchPhonePrecheckStatus()
  fetchEngineHealth()
  refreshProxyPool('', false)
  fetchCustomProxyList()
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(tickPolling, 2000)
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  stopAppsPoll()
})
</script>
