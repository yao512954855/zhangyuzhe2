<script setup lang="ts">
/**
 * 难词榜表格 —— WP-WEB-03（F8 / F9，正确率升序，低于 60% 标红）。
 *
 * 列表展示词条的：英文 / 中文 / 答题数 / 错误数 / 正确率。
 * 正确率口径由后端 `StatsService.calc_accuracy` 给出（契约 §8.1），前端只展示，不重算。
 */
import type { HardWord } from '@/api/stats'
import { formatPercent } from '@/utils/format'

defineProps<{ words: HardWord[] }>()

/** 正确率低于 60% 视为「难词」，标红提示（F8 硬要求）。 */
function isHard(w: HardWord): boolean {
  return w.counted_count > 0 && w.accuracy < 0.6
}
</script>

<template>
  <el-table :data="words" border stripe size="small" empty-text="暂无难词数据">
    <el-table-column prop="en" label="英文" min-width="120" />
    <el-table-column prop="cn" label="中文" min-width="120" />
    <el-table-column prop="answer_count" label="答题数" width="90" align="center" />
    <el-table-column prop="error_count" label="错误数" width="90" align="center" />
    <el-table-column label="正确率" width="110" align="center">
      <template #default="{ row }">
        <span :class="{ 'word-rate--hard': isHard(row) }">
          {{ formatPercent(row.accuracy, true) }}
        </span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.word-rate--hard {
  color: #f56c6c;
  font-weight: 600;
}
</style>
