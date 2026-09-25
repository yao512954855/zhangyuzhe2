<script setup lang="ts">
/**
 * 导入向导 · 步骤 3（词条预览）—— WP-WEB-02 F12
 *
 * 展示汇总（总/有效/无效/重复）+ 将新建的单元（要求用户确认是否自动创建）
 * + on_duplicate（覆盖/跳过）单选
 * + **valid_rows**（将入库的词条明细，分页）与 invalid_rows（失败行分页 + 错误报告下载）。
 *
 * 2026-09-25 起后端按契约 §6.2 返回 `valid_rows`。此前预览只给 `summary` 与
 * `invalid_rows`，管理者看得到「错在哪」却看不到「会进什么」，等于没预览。
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  downloadErrorReport as fetchErrorReport,
  downloadTemplate,
  type DuplicateStrategy,
  type WordsPreview,
} from '@/api/imports'

const props = defineProps<{ data: WordsPreview }>()
const emit = defineEmits<{
  commit: [{ createMissingUnits: boolean; onDuplicate: DuplicateStrategy }]
  back: []
}>()

const createMissingUnits = ref(true)
const onDuplicate = ref<DuplicateStrategy>('overwrite')
const invalidPage = ref(1)
const invalidPageSize = ref(10)
const validPage = ref(1)
const validPageSize = ref(10)

const pagedInvalid = () => {
  const start = (invalidPage.value - 1) * invalidPageSize.value
  return props.data.invalid_rows.slice(start, start + invalidPageSize.value)
}

/** 有效行分页：`valid_rows` 可能有几百行，一次全渲染会卡。 */
const pagedValid = () => {
  const start = (validPage.value - 1) * validPageSize.value
  return props.data.valid_rows.slice(start, start + validPageSize.value)
}

/** 类型枚举 → 中文（后端给 `word` / `phrase` / `sentence`）。 */
const typeLabel = (t: string) =>
  ({ word: '单词', phrase: '短语', sentence: '句子' })[t] || t

async function downloadErrorReport() {
  try {
    // ⚠️ 必须走 `fetchErrorReport()` 做路径归一化：
    //   后端给的是 `/api/import/report/{token}`，而 `client.baseURL` 已是 `/api`，
    //   直接透传给 `api.get()` 会拼成 `/api/api/...` 而 404
    //   —— 这正是 U6「可下载错误报告」原先不通过的根因，详见 `imports.ts` 的注释。
    const blob = await fetchErrorReport(props.data.error_report_url)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '导入错误报告.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch {
    // 具体原因已由 client 统一弹出（含 trace_id），这里只给一句可操作的提示
    ElMessage.warning('错误报告下载失败，请稍后重试或联系管理员。')
  }
}

function confirmCommit() {
  if (!createMissingUnits.value && props.data.new_units.length > 0) {
    ElMessage.warning('已取消勾选自动创建，但文件包含新单元。请勾选「自动创建新单元」或先在内容管理中建好这些单元。')
    return
  }
  emit('commit', { createMissingUnits: createMissingUnits.value, onDuplicate: onDuplicate.value })
}

function reDownloadTpl() {
  void downloadTemplate('words')
}
</script>

<template>
  <div class="word-preview">
    <!-- 汇总 -->
    <el-row :gutter="12" class="summary">
      <el-col :span="6"><el-statistic title="总行数" :value="data.summary.total" /></el-col>
      <el-col :span="6"><el-statistic title="有效" :value="data.summary.valid" /></el-col>
      <el-col :span="6"><el-statistic title="无效" :value="data.summary.invalid" /></el-col>
      <el-col :span="6"><el-statistic title="重复（将更新）" :value="data.summary.duplicated || 0" /></el-col>
    </el-row>

    <el-alert
      v-if="data.invalid_rows.length > 0"
      type="warning"
      :closable="false"
      show-icon
      style="margin: 12px 0"
    >
      共 {{ data.invalid_rows.length }} 行校验失败，将不会入库。可下载错误报告后修正，重新导入。
      <el-button link type="warning" @click="downloadErrorReport">下载错误报告</el-button>
    </el-alert>

    <!-- 将新建的单元 -->
    <el-card shadow="never" class="section">
      <template #header>将新建的单元（{{ data.new_units.length }} 个）</template>
      <template v-if="data.new_units.length === 0">
        <span class="muted">全部归属已有单元，无需新建。</span>
      </template>
      <template v-else>
        <el-checkbox v-model="createMissingUnits" class="master-check">
          自动创建以上新单元（取消勾选则需先在内容管理中建好，否则无法入库）
        </el-checkbox>
        <ul class="unit-list">
          <li v-for="(u, i) in data.new_units" :key="i">
            {{ u.subject === 'english' ? '英语' : '语文' }} · {{ u.grade }} 年级 · {{ u.semester === 'first' ? '上学期' : '下学期' }} · {{ u.name }}
          </li>
        </ul>
      </template>
    </el-card>

    <!-- on_duplicate -->
    <el-card shadow="never" class="section">
      <template #header>重复词条处理方式</template>
      <el-radio-group v-model="onDuplicate">
        <el-radio value="overwrite">覆盖（更新内容，但<b>不覆盖状态</b>）</el-radio>
        <el-radio value="skip">跳过（保留库内原词条）</el-radio>
      </el-radio-group>
    </el-card>

    <!-- valid_rows（将入库的词条明细） -->
    <el-card shadow="never" class="section">
      <template #header>将入库的词条（{{ data.valid_rows.length }} 行）</template>
      <el-table v-if="data.valid_rows.length" :data="pagedValid()" border stripe size="small">
        <el-table-column prop="row" label="行号" width="70" />
        <el-table-column prop="unit_name" label="单元" min-width="140" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ typeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column prop="en" label="英文" min-width="130" />
        <el-table-column prop="cn" label="中文" min-width="130" />
      </el-table>
      <el-pagination
        v-if="data.valid_rows.length > validPageSize"
        v-model:current-page="validPage"
        :page-size="validPageSize"
        :total="data.valid_rows.length"
        layout="prev, pager, next"
        style="margin-top: 8px; justify-content: flex-end"
      />
      <span v-else class="muted">没有有效行。</span>
    </el-card>

    <!-- invalid_rows -->
    <el-card shadow="never" class="section">
      <template #header>校验失败明细（{{ data.invalid_rows.length }} 行）</template>
      <el-table v-if="data.invalid_rows.length" :data="pagedInvalid()" border stripe size="small">
        <el-table-column prop="row" label="行号" width="80" />
        <el-table-column prop="reason" label="原因" />
      </el-table>
      <el-pagination
        v-if="data.invalid_rows.length > invalidPageSize"
        v-model:current-page="invalidPage"
        :page-size="invalidPageSize"
        :total="data.invalid_rows.length"
        layout="prev, pager, next"
        style="margin-top: 8px; justify-content: flex-end"
      />
      <span v-else class="muted">无校验失败行。</span>
    </el-card>

    <div class="actions">
      <el-button @click="emit('back')">上一步</el-button>
      <el-button link @click="reDownloadTpl">重新下载模板</el-button>
      <el-button type="primary" @click="confirmCommit">确认入库</el-button>
    </div>
  </div>
</template>

<style scoped>
.summary {
  margin-bottom: 8px;
}
.section {
  margin-bottom: 16px;
}
.unit-list {
  margin: 8px 0 0;
  padding-left: 20px;
}
.master-check {
  margin-bottom: 8px;
}
.muted {
  color: var(--admin-text-secondary);
}
.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
