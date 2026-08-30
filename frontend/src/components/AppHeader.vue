<template>
  <header class="ce-header">
    <div class="ce-header-row">
      <div class="ce-brand">
        <div class="ce-logo">ENA</div>
        <div>
          <div class="mark">
            <h1>CYBER EDGE NODE</h1>
            <span class="ce-badge is-info">v2.2</span>
          </div>
          <div class="sub">EdgeNode-Auditor · 墨绿暗黑状态机审计控制台</div>
        </div>
      </div>

      <div class="ce-header-actions">
        <div class="ce-chips">
          <div class="ce-chip" :class="engineChipClass">
            <span class="ce-dot" :class="engineDotClass"></span>
            <span>引擎 {{ engineHealth.status === 'ok' ? '就绪' : (engineHealth.status === 'off' ? '离线' : '探测中') }} · 8000</span>
          </div>
          <div class="ce-chip" :class="precheckChipClass">
            <span class="ce-dot" :class="precheckDotClass"></span>
            <span>白号预检 {{ precheckLabel }}</span>
          </div>
          <div class="ce-chip" :class="proxyChipClass">
            <span class="ce-dot" :class="proxyDotClass"></span>
            <span>代理池 {{ customProxySummaryText }} · 动态 {{ proxyPool.length }}</span>
          </div>
        </div>
        <div class="ce-session">
          <span class="ce-chip">{{ authUser || 'console' }}</span>
          <button type="button" class="ce-btn-ghost" @click="onLogout">退出</button>
        </div>
      </div>
    </div>

    <nav class="ce-nav">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        class="ce-nav-item"
        :class="{ 'is-active': activeTab === tab.id }"
        @click="goTab(tab.id)"
      >
        <component :is="iconMap[tab.icon]" :size="14" :stroke-width="1.8" />
        <span>{{ tab.name }}</span>
      </button>
    </nav>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { Zap, Shield, Globe, SlidersHorizontal, Smartphone, KeyRound, Ban } from 'lucide-vue-next'
import { useUi } from '../composables/useUi'
import { useTasks } from '../composables/useTasks'
import { useProxy } from '../composables/useProxy'
import { authUser, logout } from '../composables/useAuth'

const { tabs, activeTab, engineHealth, goTab } = useUi()
const { phonePrecheckStatus } = useTasks()
const { customProxySummaryText, proxyPool, customProxies } = useProxy()

const onLogout = () => {
  logout()
}

const iconMap = {
  zap: Zap,
  shield: Shield,
  key: KeyRound,
  ban: Ban,
  globe: Globe,
  sliders: SlidersHorizontal,
  smartphone: Smartphone
}

const engineChipClass = computed(() => ({
  'is-ok': engineHealth.value.status === 'ok',
  'is-off': engineHealth.value.status === 'off'
}))
const engineDotClass = computed(() => ({
  'is-ok': engineHealth.value.status === 'ok',
  'is-off': engineHealth.value.status === 'off',
  'is-warn': engineHealth.value.status === 'pending'
}))

const precheckLabel = computed(() => {
  const s = phonePrecheckStatus.value
  if (s.active) return '激活'
  if (s.enabled === false) return '关闭'
  return '降级'
})
const precheckChipClass = computed(() => ({
  'is-ok': phonePrecheckStatus.value.active,
  'is-warn': !phonePrecheckStatus.value.active && phonePrecheckStatus.value.enabled !== false,
  'is-off': phonePrecheckStatus.value.enabled === false
}))
const precheckDotClass = computed(() => ({
  'is-ok': phonePrecheckStatus.value.active,
  'is-warn': !phonePrecheckStatus.value.active && phonePrecheckStatus.value.enabled !== false,
  'is-off': phonePrecheckStatus.value.enabled === false
}))

const proxyHealthy = computed(() => customProxies.value.some((p) => p.healthy === true) || proxyPool.value.some((p) => p.healthy === true))
const proxyChipClass = computed(() => ({
  'is-ok': proxyHealthy.value,
  'is-warn': !proxyHealthy.value && (customProxies.value.length > 0 || proxyPool.value.length > 0)
}))
const proxyDotClass = computed(() => ({
  'is-ok': proxyHealthy.value,
  'is-warn': !proxyHealthy.value
}))
</script>
