<script setup lang="ts">
/**
 * 加载占位 —— WP-WEB-01 F8
 *
 * 用法：
 *   <LoadingBox :loading="loading" :rows="6">
 *     <el-table ... />
 *   </LoadingBox>
 *
 * 「首次加载」与「已有数据后刷新」体感不同：前者铺骨架屏，后者只在右上角转圈，
 * 避免整页内容闪烁（`hasData` 用来区分这两种情况）。
 */
import { Loading } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    /** 是否正在加载 */
    loading?: boolean
    /** 骨架行数 */
    rows?: number
    /** 加载中最小高度（px） */
    minHeight?: number
    /** 是否已有数据：true 且 loading 时只显示右上角转圈，不铺骨架 */
    hasData?: boolean
  }>(),
  {
    loading: false,
    rows: 4,
    minHeight: 160,
    hasData: false,
  },
)
</script>

<template>
  <div class="loading-box" :style="{ minHeight: `${minHeight}px` }">
    <div v-if="loading && hasData" class="loading-box__mask">
      <el-icon class="is-loading" :size="20"><Loading /></el-icon>
    </div>

    <el-skeleton v-if="loading && !hasData" :rows="rows" animated />

    <slot v-else />
  </div>
</template>

<style scoped>
.loading-box {
  position: relative;
}

.loading-box__mask {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: rgb(255 255 255 / 88%);
  box-shadow: 0 2px 8px rgb(0 0 0 / 8%);
}
</style>
