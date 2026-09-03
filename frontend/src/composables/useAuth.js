import { ref } from 'vue'

export const isAuthenticated = ref(false)
export const authUser = ref('')
export const authError = ref('')
export const authReady = ref(false)

const jsonOrEmpty = async (res) => {
  try {
    return await res.json()
  } catch {
    return {}
  }
}

export const fetchMe = async () => {
  try {
    const res = await fetch('/api/auth/me', { credentials: 'include' })
    if (res.ok) {
      const data = await jsonOrEmpty(res)
      isAuthenticated.value = true
      authUser.value = data.username || ''
      authError.value = ''
      return true
    }
    isAuthenticated.value = false
    authUser.value = ''
    return false
  } catch {
    isAuthenticated.value = false
    authUser.value = ''
    return false
  } finally {
    authReady.value = true
  }
}

export const login = async (username, password) => {
  authError.value = ''
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password })
    })
    const data = await jsonOrEmpty(res)
    if (!res.ok) {
      isAuthenticated.value = false
      authUser.value = ''
      authError.value = data.detail || '用户名或密码错误'
      return false
    }
    isAuthenticated.value = true
    authUser.value = data.username || username
    authError.value = ''
    return true
  } catch (e) {
    isAuthenticated.value = false
    authUser.value = ''
    authError.value = e.message || '登录失败'
    return false
  }
}

export const logout = async () => {
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include'
    })
  } catch {
    /* 本地状态仍清掉 */
  }
  isAuthenticated.value = false
  authUser.value = ''
  authError.value = ''
}

export const useAuth = () => ({
  isAuthenticated,
  authUser,
  authError,
  authReady,
  login,
  logout,
  fetchMe
})
