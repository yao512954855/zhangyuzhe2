<script setup lang="ts">
/**
 * 导入向导 · 步骤 4（结果）—— WP-WEB-02 F14
 *
 * 显示 created/updated/skipped（words 含 skipped；texts 无 skipped）。
 * 「继续导入」清空向导重来；「查看内容」跳到单元列表（新单元/内容立即可见，见交付报告偏差）。
 */
import { useRouter } from 'vue-router'

import { type ImportType, type TextsCommitResult, type WordsCommitResult } from '@/api/imports'

defineProps<{
  type: ImportType
  result: WordsCommitResult | TextsCommitResult
}>()
const emit = defineEmits<{ continue: [] }>()

const router = useRouter()

function viewContent() {
  router.push('/content/units')
}
</script>

<template>
  <div class="result">
    <el-result icon="success" title="导入完成" sub-title="内容已入库，学生端可立即使用">
      <template #extra>
        <el-row :gutter="12" class="metrics">
          <el-col :span="6">
            <el-statistic title="新增" :value="(result as WordsCommitResult).created ?? (result as TextsCommitResult).created" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="更新" :value="(result as WordsCommitResult).updated ?? (result as TextsCommitResult).updated" />
          </el-col>
          <el-col v-if="type === 'words'" :span="6">
            <el-statistic title="跳过" :value="(result as WordsCommitResult).skipped" />
          </el-col>
          <el-col :span="6">
            <el-statistic title="新建单元" :value="(result as WordsCommitResult).units_created ?? (result as TextsCommitResult).units_created" />
          </el-col>
        </el-row>
      </template>
    </el-result>

    <div class="actions">
      <el-button @click="emit('continue')">继续导入</el-button>
      <el-button type="primary" @click="viewContent">查看内容</el-button>
    </div>
  </div>
</template>

<style scoped>
.metrics {
  margin-top: 8px;
}
.actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 8px;
}
</style>
