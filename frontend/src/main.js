import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import { isAuthenticated } from './composables/useAuth'

const nativeFetch = window.fetch.bind(window)

const requestPath = (input) => {
  const raw = typeof input === 'string' ? input : (input && input.url) || ''
  try {
    return new URL(raw, window.location.origin).pathname
  } catch {
    return String(raw)
  }
}

window.fetch = (input, init) => {
  const path = requestPath(input)
  const opts = init ? { ...init } : {}
  if (path.startsWith('/api') && opts.credentials == null) {
    opts.credentials = 'include'
  }
  return nativeFetch(input, opts).then((res) => {
    if (
      res.status === 401 &&
      path.startsWith('/api') &&
      path !== '/api/auth/login' &&
      path !== '/api/health'
    ) {
      isAuthenticated.value = false
    }
    return res
  })
}

createApp(App).mount('#app')
