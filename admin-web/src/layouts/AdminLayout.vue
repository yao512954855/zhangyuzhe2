<script setup lang="ts">
/**
 * 管理端主布局 —— WP-WEB-01 F6
 *
 * 菜单**由路由元信息生成**（`meta.title` / `meta.icon` / `meta.order`），
 * 所以 WEB-02/03 新增路由文件后菜单会自动出现，不需要动这个组件（验收用例 U8）。
 *
 * 这里用 `router.getRoutes()` 而不是 import 业务路由数组，是为了避开
 * `router/index.ts → AdminLayout.vue → router/index.ts` 的循环依赖。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Expand, Fold } from '@element-plus/icons-vue'

import { useUserStore } from '@/stores/user'

interface MenuItem {
  path: string
  title: string
  icon?: string
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const collapsed = ref(false)

const menuItems = computed<MenuItem[]>(() => {
  const seen = new Set<string>()
  return (
    router
      .getRoutes()
      .filter((record) => {
        const meta = record.meta
        // 只认「有标题 + 非公开 + 非隐藏」，且按 path 去重
        if (!meta?.title || meta.public || meta.hidden) return false
        if (seen.has(record.path)) return false
        seen.add(record.path)
        return true
      })
      .sort((a, b) => Number(a.meta.order ?? 999) - Number(b.meta.order ?? 999))
      .map((record) => ({
        path: record.path,
        title: String(record.meta.title),
        icon: record.meta.icon,
      }))
  )
})

const activeMenu = computed(() => route.path)

async function handleLogout(): Promise<void> {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      type: 'warning',
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户点了取消
  }
  userStore.logout()
  await router.replace('/login')
}
</script>

<template>
  <el-container class="admin-layout">
    <el-aside :width="collapsed ? '64px' : '220px'" class="admin-aside">
      <div class="admin-brand">
        <span class="admin-brand__logo">背</span>
        <span v-show="!collapsed" class="admin-brand__text">背记系统 · 管理端</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        class="admin-menu"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
          <template #title>{{ item.title }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <el-button text class="admin-header__toggle" @click="collapsed = !collapsed">
          <el-icon :size="18">
            <Fold v-if="!collapsed" />
            <Expand v-else />
          </el-icon>
        </el-button>

        <div class="admin-header__right">
          <span class="admin-header__user">{{ userStore.displayName }}</span>
          <el-button text type="primary" @click="handleLogout">退出登录</el-button>
        </div>
      </el-header>

      <el-main class="admin-main">
        <router-view v-slot="{ Component }">
          <keep-alive :max="6">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.admin-layout {
  height: 100vh;
}

.admin-aside {
  display: flex;
  flex-direction: column;
  background: var(--admin-panel);
  border-right: 1px solid var(--admin-border);
  transition: width 0.2s;
  overflow: hidden;
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--admin-header-height);
  padding: 0 16px;
  border-bottom: 1px solid var(--admin-border);
  white-space: nowrap;
}

.admin-brand__logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  border-radius: 6px;
  background: var(--admin-brand);
  color: #fff;
  font-weight: 700;
}

.admin-brand__text {
  font-size: 15px;
  font-weight: 600;
}

.admin-menu {
  flex: 1;
  border-right: none;
  overflow-x: hidden;
}

.admin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--admin-header-height);
  padding: 0 16px;
  background: var(--admin-panel);
  border-bottom: 1px solid var(--admin-border);
}

.admin-header__toggle {
  padding: 6px;
}

.admin-header__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.admin-header__user {
  color: var(--admin-text-secondary);
}

.admin-main {
  padding: 0;
  background: var(--admin-bg);
  overflow: auto;
}
</style>
