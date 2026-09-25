<script setup lang="ts">
/**
 * 工作台首页 —— WP-WEB-01 F7
 *
 * 「服务地址与二维码卡片」由 **WP-WEB-03** 实现（F12：同一组件两处复用）。
 *
 * ⚠️ WEB-03 收口时的改动（**最小加法**，仅接线，未改本文件其它逻辑）：
 *   - 挂载点占位已按 WEB-01 的约定替换为真实组件；
 *   - WEB-01 注释里写的组件名是 `ServiceAddressCard`，而 WEB-03 规格文件清单里
 *     定的名字是 `ServerQRCard.vue` —— 按**规格**取名，这里 import 的即 `ServerQRCard`。
 *     （该命名不一致已记入 `docs/dev/02-实现约定与复用经验.md` §八 偏离清单。）
 */
import { computed } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import ServerQRCard from '@/components/ServerQRCard.vue'
import { useUserStore } from '@/stores/user'
import { formatDateTime } from '@/utils/format'

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
</script>

<template>
  <div class="admin-page">
    <PageHeader title="工作台" :subtitle="`${greeting}，${userStore.displayName} · ${today}`" />

    <el-row :gutter="16">
      <el-col :span="16">
        <!-- WP-WEB-03 F12：服务地址与二维码卡片（与「服务地址」页复用同一组件） -->
        <el-card shadow="never" class="dashboard-card">
          <template #header>
            <span class="dashboard-placeholder__title">服务地址</span>
          </template>
          <ServerQRCard />
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card shadow="never">
          <template #header>
            <span class="dashboard-placeholder__title">当前登录</span>
          </template>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="账号">
              {{ userStore.user?.username ?? '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="姓名">
              {{ userStore.user?.real_name ?? '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="角色">
              <el-tag size="small" type="success">管理者</el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.dashboard-card {
  min-height: 260px;
}

.dashboard-placeholder__title {
  font-weight: 600;
}
</style>
