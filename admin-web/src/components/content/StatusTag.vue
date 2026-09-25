<script setup lang="ts">
/**
 * 状态标签 —— WP-WEB-02 共用。
 * 统一渲染 active/inactive（启用/停用），不重复散落在各页面。
 */
import type { ContentStatus } from '@/api/content'

const props = defineProps<{
  status: ContentStatus
  /** 显示「启用/停用」中文；传 true 则显示「活跃/停用」等更中性的词 */
  text?: string
}>()

function label(status: ContentStatus, custom?: string): string {
  if (custom) return custom
  return status === 'active' ? '启用' : '停用'
}
</script>

<template>
  <el-tag :type="props.status === 'active' ? 'success' : 'info'" size="small" effect="light">
    {{ label(props.status, props.text) }}
  </el-tag>
</template>
