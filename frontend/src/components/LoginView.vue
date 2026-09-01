<template>
  <div class="ce-login">
    <form class="ce-login-card ce-panel" @submit.prevent="onSubmit">
      <div class="ce-login-brand">
        <div class="ce-logo">ENA</div>
        <div>
          <h1>CYBER EDGE NODE</h1>
          <p class="sub">登录控制台</p>
        </div>
      </div>

      <label class="ce-label" for="ce-login-user">用户名</label>
      <input
        id="ce-login-user"
        class="ce-input"
        v-model="username"
        name="username"
        autocomplete="username"
        autocapitalize="none"
        spellcheck="false"
        required
      />

      <label class="ce-label" for="ce-login-pass">密码</label>
      <input
        id="ce-login-pass"
        class="ce-input"
        v-model="password"
        type="password"
        name="password"
        autocomplete="current-password"
        required
      />

      <div v-if="authError" class="ce-alert is-danger">{{ authError }}</div>

      <button class="ce-btn ce-login-submit" type="submit" :disabled="busy">
        {{ busy ? '登录中…' : '登录' }}
      </button>

      <div class="ce-login-health">
        <span class="ce-dot" :class="engineDotClass"></span>
        引擎 {{ engineLabel }}
      </div>
    </form>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useAuth } from '../composables/useAuth'
import { useUi } from '../composables/useUi'

const { authError, login } = useAuth()
const { engineHealth, fetchEngineHealth } = useUi()

const username = ref('')
const password = ref('')
const busy = ref(false)

const engineLabel = computed(() => {
  const status = engineHealth.value.status
  if (status === 'ok') return '就绪'
  if (status === 'off') return '离线'
  return '探测中'
})

const engineDotClass = computed(() => ({
  'is-ok': engineHealth.value.status === 'ok',
  'is-off': engineHealth.value.status === 'off',
  'is-warn': engineHealth.value.status === 'pending'
}))

onMounted(() => {
  fetchEngineHealth()
})

const onSubmit = async () => {
  if (busy.value) return
  busy.value = true
  try {
    await login(username.value.trim(), password.value)
  } finally {
    busy.value = false
  }
}
</script>
