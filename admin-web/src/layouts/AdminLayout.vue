<script setup lang="ts">
/**
 * 管理端主布局 —— WP-WEB-01 F6
 *
 * 菜单**由路由元信息生成**（`meta.title` / `meta.icon` / `meta.order` / `meta.group`），
 * 所以 WEB-02/03 新增路由文件后菜单会自动出现，不需要动这个组件（验收用例 U8）。
 *
 * 分组渲染（可折叠菜单）规则：
 * - 组内 **≥2 项** → `el-sub-menu`，点分组标题展开/收起；
 * - 组内 **仅 1 项** → 直接平铺 `el-menu-item`（单项再套一层折叠没有意义）；
 * - 组间顺序取组内最小 `order`；未填 `group` 的路由归入「其他」；
 * - 侧边栏整体收起（`:collapse`）时，`el-sub-menu` 自动降级为悬停弹出的浮层子菜单。
 * - 「已展开的分组」与「侧边栏折叠态」都持久化到 localStorage；
 *   路由切换（含页面内跳转）时自动展开当前项所在组，避免当前项藏在收起的组里。
 *
 * 这里用 `router.getRoutes()` 而不是 import 业务路由数组，是为了避开
 * `router/index.ts → AdminLayout.vue → router/index.ts` 的循环依赖。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import type { MenuInstance } from 'element-plus'
import { Expand, Fold } from '@element-plus/icons-vue'

import { useUserStore } from '@/stores/user'

interface MenuItem {
  path: string
  title: string
  icon?: string
}

interface MenuGroup {
  /** 分组名（来自路由 `meta.group`） */
  name: string
  /** 组内最小 order，决定组间先后 */
  order: number
  /** 分组图标（折叠父级菜单的触发图标） */
  icon: string
  items: MenuItem[]
  /** 组内仅 1 项时指向该项 —— 此时直接平铺，不做父级折叠 */
  single: MenuItem | null
}

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 未填写 `meta.group` 的兜底分组名（保证新增路由不会掉出菜单） */
const FALLBACK_GROUP = '其他'

/**
 * 分组图标。分组是「布局层」概念，所以图标映射放这里，
 * 不塞进路由 `meta`（否则 8 个路由文件都要加一个只有菜单用的字段）。
 * 未登记的分组兜底 `Menu`。
 */
const GROUP_ICONS: Record<string, string> = {
  总览: 'Odometer',
  内容: 'Folder',
  学生: 'User',
  作业与统计: 'DataAnalysis',
  大屏: 'Monitor',
  系统: 'Setting',
}

const STORAGE_KEY_OPENED = 'bjsys_admin_menu_opened'
const STORAGE_KEY_COLLAPSED = 'bjsys_admin_sidebar_collapsed'

function readStringArray(key: string): string[] {
  try {
    const raw = localStorage.getItem(key)
    const parsed = raw ? JSON.parse(raw) : null
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}

const collapsed = ref(localStorage.getItem(STORAGE_KEY_COLLAPSED) === '1')

/** 已展开的分组名（持久化，收起侧边栏时不参与记录） */
const openedGroups = ref<string[]>(readStringArray(STORAGE_KEY_OPENED))

const menuGroups = computed<MenuGroup[]>(() => {
  const seen = new Set<string>()
  const buckets = new Map<string, { order: number; items: MenuItem[] }>()

  const records = router
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

  for (const record of records) {
    const name = String(record.meta.group ?? FALLBACK_GROUP)
    let bucket = buckets.get(name)
    if (!bucket) {
      bucket = { order: Number.MAX_SAFE_INTEGER, items: [] }
      buckets.set(name, bucket)
    }
    bucket.order = Math.min(bucket.order, Number(record.meta.order ?? 999))
    bucket.items.push({
      path: record.path,
      title: String(record.meta.title),
      icon: record.meta.icon,
    })
  }

  return [...buckets.entries()]
    .map(([name, bucket]) => ({
      name,
      order: bucket.order,
      icon: GROUP_ICONS[name] ?? 'Menu',
      items: bucket.items,
      single: bucket.items.length === 1 ? bucket.items[0] : null,
    }))
    .sort((a, b) => a.order - b.order)
})

/** 当前路由所在「可折叠组」的名字（单项组本身就平铺，无需展开） */
const activeGroupName = computed(() => {
  const hit = menuGroups.value.find((g) => g.items.some((it) => it.path === route.path))
  return hit && hit.items.length > 1 ? hit.name : ''
})

const menuRef = ref<MenuInstance | null>(null)

function persistOpened(): void {
  try {
    localStorage.setItem(STORAGE_KEY_OPENED, JSON.stringify(openedGroups.value))
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

function handleMenuOpen(index: string): void {
  // 侧边栏整体收起时是浮层弹出，不污染「展开的分组」记忆
  if (collapsed.value) return
  if (!openedGroups.value.includes(index)) openedGroups.value = [...openedGroups.value, index]
  persistOpened()
}

function handleMenuClose(index: string): void {
  if (collapsed.value) return
  openedGroups.value = openedGroups.value.filter((v) => v !== index)
  persistOpened()
}

function handleToggleCollapse(): void {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(STORAGE_KEY_COLLAPSED, collapsed.value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

onMounted(() => {
  // 首次进入（没有历史记忆）只展开当前路由所在组，其余保持收起
  if (openedGroups.value.length === 0 && activeGroupName.value) {
    openedGroups.value = [activeGroupName.value]
  }
  for (const name of openedGroups.value) menuRef.value?.open(name)
})

// 路由切换时自动展开当前项所在组，避免「当前项藏在收起的组里」
watch(activeGroupName, (name) => {
  if (!name || collapsed.value) return
  if (!openedGroups.value.includes(name)) {
    openedGroups.value = [...openedGroups.value, name]
    persistOpened()
  }
  menuRef.value?.open(name)
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
        ref="menuRef"
        :default-active="activeMenu"
        :collapse="collapsed"
        router
        class="admin-menu"
        @open="handleMenuOpen"
        @close="handleMenuClose"
      >
        <template v-for="group in menuGroups" :key="group.name">
          <!-- 单项组：直接平铺成菜单项（点开只有一个孩子的折叠反而不工业级） -->
          <el-menu-item v-if="group.single" :index="group.single.path">
            <el-icon v-if="group.single.icon"><component :is="group.single.icon" /></el-icon>
            <template #title>{{ group.single.title }}</template>
          </el-menu-item>

          <!-- 多项组：可折叠的父级菜单 -->
          <el-sub-menu v-else :index="group.name">
            <template #title>
              <el-icon><component :is="group.icon" /></el-icon>
              <span class="admin-menu__group-title">{{ group.name }}</span>
            </template>
            <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
              <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
              <template #title>{{ item.title }}</template>
            </el-menu-item>
          </el-sub-menu>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="admin-header">
        <el-button text class="admin-header__toggle" @click="handleToggleCollapse">
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
  overflow-y: auto;
}

/* 折叠父级的分组名；侧边栏整体收起时由 Element Plus 自动隐去文字只留图标 */
.admin-menu__group-title {
  font-weight: 600;
  letter-spacing: 0.5px;
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
