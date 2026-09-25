<script setup lang="ts">
/**
 * 登录页 —— WP-WEB-01 F4
 *
 * 要点：
 * - `role` 固定 `admin`（由 store 负责拼），学生账号会被后端拒（验收用例 U4）；
 * - 错误提示**由 api/client.ts 统一弹出**，本页不再重复弹（验收用例 U6），
 *   这里只把带 trace_id 的失败信息留一份到控制台便于排查。
 */
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'

import { BizError } from '@/api/client'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const formRef = ref<FormInstance>()
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleSubmit(): Promise<void> {
  if (!formRef.value) return

  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login(form.username.trim(), form.password)
    ElMessage.success('登录成功')

    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    await router.replace(redirect)
  } catch (error) {
    // 提示已由 client 弹过；这里只留排查线索
    if (error instanceof BizError) {
      console.warn('[登录失败]', { code: error.code, traceId: error.traceId, message: error.message })
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-card__head">
        <div class="login-card__logo">背</div>
        <h1 class="login-card__title">背记系统 管理端</h1>
        <p class="login-card__subtitle">中小学英语单词 / 语文课文背诵</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" size="large" @submit.prevent="handleSubmit">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" clearable />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleSubmit"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" class="login-card__submit" :loading="loading" @click="handleSubmit">
            登录
          </el-button>
        </el-form-item>
      </el-form>

      <p class="login-card__tip">仅限管理者账号登录</p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100vh;
  background: linear-gradient(135deg, #eef3ff 0%, #f5f7fa 55%, #eaf1ff 100%);
}

.login-card {
  width: 380px;
  padding: 32px 32px 20px;
  background: var(--admin-panel);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgb(47 111 237 / 10%);
}

.login-card__head {
  margin-bottom: 24px;
  text-align: center;
}

.login-card__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border-radius: 10px;
  background: var(--admin-brand);
  color: #fff;
  font-size: 22px;
  font-weight: 700;
}

.login-card__title {
  margin: 12px 0 4px;
  font-size: 20px;
  font-weight: 600;
}

.login-card__subtitle {
  margin: 0;
  color: var(--admin-text-secondary);
  font-size: 13px;
}

.login-card__submit {
  width: 100%;
}

.login-card__tip {
  margin: 0 0 8px;
  text-align: center;
  color: var(--admin-text-secondary);
  font-size: 12px;
}
</style>
