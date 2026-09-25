<script setup lang="ts">
/**
 * 空状态 —— WP-WEB-01 F8
 *
 * 用法（默认插槽放操作按钮）：
 *   <EmptyState description="还没有学生">
 *     <el-button type="primary">批量导入</el-button>
 *   </EmptyState>
 */
withDefaults(
  defineProps<{
    description?: string
    imageSize?: number
  }>(),
  {
    description: '暂无数据',
    imageSize: 80,
  },
)
</script>

<template>
  <div class="empty-state">
    <el-empty :description="description" :image-size="imageSize">
      <slot />
    </el-empty>
  </div>
</template>

<style scoped>
.empty-state {
  padding: 16px 0;
}
</style>
