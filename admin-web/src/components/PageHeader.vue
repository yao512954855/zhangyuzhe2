<script setup lang="ts">
/**
 * 页头：标题 + 副标题 + 右侧操作区 —— WP-WEB-01 F8
 *
 * 用法：
 *   <PageHeader title="学生管理" subtitle="共 128 人">
 *     <el-button type="primary">新建</el-button>
 *   </PageHeader>
 */
defineProps<{
  title: string
  subtitle?: string
}>()
</script>

<template>
  <div class="page-header">
    <div class="page-header__left">
      <h2 class="page-header__title">{{ title }}</h2>
      <span v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</span>
    </div>

    <div class="page-header__actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header__left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.page-header__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.page-header__subtitle {
  color: var(--admin-text-secondary);
  font-size: 13px;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}
</style>
