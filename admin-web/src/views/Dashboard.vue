<script setup lang="ts">
/**
 * 工作台首页 —— WP-WEB-01 F7
 *
 * 「服务地址与二维码卡片」由 **WP-WEB-03** 实现（F12：同一组件两处复用）。
 *
 * 布局（2026-09-30 姚工要求重排，工业级控制台模式）：
 *   - 第一行：服务地址主卡（全宽）—— 这页唯一的「运维级」信息，二维码左、地址键值右；
 *   - 第二行：快捷入口宫格（span 16）+ 当前登录紧凑卡（span 8）——
 *     登录信息在顶部导航已有姓名+退出，页面内只保留一份紧凑摘要，不再独占一整列。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import ServerQRCard from '@/components/ServerQRCard.vue'
import { useUserStore } from '@/stores/user'
import { formatDateTime } from '@/utils/format'

const router = useRouter()
const userStore = useUserStore()

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '上午好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const today = computed(() => formatDateTime(new Date().toISOString()).slice(0, 10))

/** 快捷入口（控制台 quick actions 模式）：按管理端高频操作挑 6 个 */
const quickLinks: Array<{ title: string; desc: string; to: string }> = [
  { title: '单元与课文', desc: '内容管理', to: '/content/units' },
  { title: '内容导入', desc: '词条 / 课文', to: '/content/import' },
  { title: '学生导入', desc: '名单批量导入', to: '/students/import' },
  { title: '作业布置', desc: '布置 / 查看完成', to: '/homework' },
  { title: '班级概况', desc: '成绩统计', to: '/stats/class' },
  { title: '实时大屏', desc: '课堂投屏', to: '/live-board' },
]

const realName = computed(() => userStore.user?.real_name || userStore.displayName)
const avatarChar = computed(() => realName.value.slice(0, 1))
</script>

<template>
  <div class="admin-page">
    <PageHeader title="工作台" :subtitle="`${greeting}，${userStore.displayName} · ${today}`" />

    <!-- 第一行：服务地址主卡（全宽） -->
    <el-card shadow="never" class="dashboard-card">
      <template #header>
        <span class="dashboard-card__title">服务地址</span>
      </template>
      <!-- WP-WEB-03 F12：服务地址与二维码卡片（与「服务地址」页复用同一组件） -->
      <ServerQRCard />
    </el-card>

    <!-- 第二行：快捷入口 + 当前登录 -->
    <el-row :gutter="16" class="dashboard-row">
      <el-col :span="16">
        <el-card shadow="never" class="dashboard-card">
          <template #header>
            <span class="dashboard-card__title">快捷入口</span>
          </template>
          <div class="quick-grid">
            <button
              v-for="link in quickLinks"
              :key="link.to"
              type="button"
              class="quick-grid__item"
              @click="router.push(link.to)"
            >
              <span class="quick-grid__title">{{ link.title }}</span>
              <span class="quick-grid__desc">{{ link.desc }}</span>
            </button>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card shadow="never" class="dashboard-card">
          <template #header>
            <span class="dashboard-card__title">当前登录</span>
          </template>
          <div class="login-brief">
            <div class="login-brief__avatar">{{ avatarChar }}</div>
            <div class="login-brief__meta">
              <div class="login-brief__name-row">
                <span class="login-brief__name">{{ realName }}</span>
                <el-tag size="small" type="success">管理者</el-tag>
              </div>
              <div class="login-brief__account">账号：{{ userStore.user?.username ?? '-' }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.dashboard-card {
  min-height: 200px;
}

.dashboard-row {
  margin-top: 16px;
}

.dashboard-card__title {
  font-weight: 600;
}

/* ---- 快捷入口宫格 ---- */
.quick-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.quick-grid__item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid var(--admin-border, #ebeef5);
  border-radius: 8px;
  background: var(--admin-bg, #fff);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.quick-grid__item:hover {
  border-color: var(--el-color-primary-light-5, #a0cfff);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
.quick-grid__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--admin-text, #303133);
}
.quick-grid__desc {
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}

/* ---- 当前登录（紧凑版） ---- */
.login-brief {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 0;
}
.login-brief__avatar {
  flex: 0 0 auto;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: var(--el-color-primary-light-8, #ecf5ff);
  color: var(--el-color-primary, #409eff);
  font-size: 20px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.login-brief__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.login-brief__name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.login-brief__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--admin-text, #303133);
}
.login-brief__account {
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}
</style>
