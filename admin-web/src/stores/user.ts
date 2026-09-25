/**
 * 用户状态 —— WP-WEB-01 F4 / F5
 *
 * token 落 localStorage（验收用例 U9：刷新后登录态保持）；
 * user 一并持久化，避免刷新后顶部栏用户名空掉。
 * 管理端**不用** `/api/me/profile` 去回捞：那属于 BE-02，且没必要为了用户名多一次请求。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api, clearToken, getToken, setToken } from '@/api/client'
import type { LoginPayload, LoginResult, UserInfo } from '@/api/types'

const USER_KEY = 'bjsys_admin_user'

function loadUser(): UserInfo | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as UserInfo) : null
  } catch {
    return null
  }
}

function saveUser(user: UserInfo | null): void {
  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } else {
    localStorage.removeItem(USER_KEY)
  }
}

export const useUserStore = defineStore('user', () => {
  const token = ref<string>(getToken())
  const user = ref<UserInfo | null>(loadUser())

  const isLoggedIn = computed(() => Boolean(token.value))

  const displayName = computed(() => user.value?.real_name || user.value?.username || '未登录')

  /** 登录。`role` 固定 `admin`（WEB-01 F4，学生账号会被后端拒）。 */
  async function login(username: string, password: string): Promise<void> {
    const payload: LoginPayload = { username, password, role: 'admin' }
    const result = await api.post<LoginResult>('/auth/login', payload)

    token.value = result.access_token
    user.value = result.user
    setToken(result.access_token)
    saveUser(result.user)
  }

  /** 清本地登录态（不调后端；JWT 无状态，退出即清）。 */
  function logout(): void {
    token.value = ''
    user.value = null
    clearToken()
    saveUser(null)
  }

  return { token, user, isLoggedIn, displayName, login, logout }
})
