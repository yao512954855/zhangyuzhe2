<script setup lang="ts">
/**
 * 词条表格 —— WP-WEB-02 F5/F6/F7/F8
 *
 * F5 列：序号/类型/英文/中文/可接受答案/音标/是否拼读/状态；支持批量勾选。
 * F6 批量启停：选中后批量启用/停用，显示成功条数。
 * F7 编辑：弹窗表单；保存时后端 history_affected=true → 二次确认。
 * F8 删除：后端 30003 → 提示记录数并建议停用。
 *
 * 分页（F4 之一）：服务端分页，支持 20/50/100 切换。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import StatusTag from '@/components/content/StatusTag.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import WordEditDialog from './WordEditDialog.vue'
import {
  batchWordStatus,
  deleteWord,
  listUnitWords,
  patchWord,
  type WordItem,
} from '@/api/content'
import { BizError } from '@/api/client'

const props = defineProps<{ unitId: number }>()

const WORD_TYPE_LABEL: Record<string, string> = { word: '单词', phrase: '短语', sentence: '句子' }

const loading = ref(false)
const loaded = ref(false)
const words = ref<WordItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const selected = ref<WordItem[]>([])

const editDialog = reactive<{ visible: boolean; word: WordItem | null }>({ visible: false, word: null })

async function load() {
  loading.value = true
  try {
    const res = await listUnitWords(props.unitId, { page: page.value, page_size: pageSize.value })
    words.value = res.items
    total.value = res.total
    loaded.value = true
  } catch {
    // client 统一提示
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  load()
}
function onSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  load()
}

function onSelectionChange(rows: WordItem[]) {
  selected.value = rows
}

// ----------------------------- F6 批量启停
const batching = ref(false)
async function batchToggle(status: 'active' | 'inactive') {
  if (selected.value.length === 0) {
    ElMessage.warning('请先勾选要操作的词条')
    return
  }
  const verb = status === 'active' ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(
      `确定${verb}选中的 ${selected.value.length} 个词条？${verb === '停用' ? '停用后学生端不再出题，但保留历史记录。' : ''}`,
      `批量${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batching.value = true
  try {
    const res = await batchWordStatus(
      selected.value.map((w) => w.id),
      status,
    )
    ElMessage.success(`已${verb} ${res.updated}/${res.requested} 个词条`)
    selected.value = []
    load()
  } catch {
    // 统一提示
  } finally {
    batching.value = false
  }
}

// ----------------------------- F7 编辑
function openEdit(row: WordItem) {
  editDialog.word = row
  editDialog.visible = true
}

/**
 * F7 编辑保存：由父组件统一调 patchWord 并据返回值的 history_affected 处理二次确认。
 * 后端在保存成功后才告知是否影响历史，因此此处为「保存后告知」（而非拦截式二次确认），
 * 提示用户历史成绩快照不受影响。
 */
async function handleSave(payload: { id: number; data: Parameters<typeof patchWord>[1] }) {
  try {
    const res = await patchWord(payload.id, payload.data)
    if (res.history_affected) {
      await ElMessageBox.alert(
        '该词条已有练习记录，已保存修改；历史成绩快照保持不变，不受影响。',
        '已有练习记录',
        { type: 'info', confirmButtonText: '我知道了' },
      ).catch(() => {})
    } else {
      ElMessage.success('已保存')
    }
    editDialog.visible = false
    load()
  } catch {
    // 统一提示
  }
}

// ----------------------------- F8 删除
async function removeWord(row: WordItem) {
  try {
    await ElMessageBox.confirm(
      '删除会彻底移除该词条（不可恢复）。确定删除？',
      '确认删除',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteWord(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e instanceof BizError && e.code === 30003) {
      const count = (e.data as { record_count?: number } | null)?.record_count
      await showWordHasRecords(row, count)
    }
  }
}

async function showWordHasRecords(row: WordItem, count?: number) {
  const text = count !== undefined ? `（共 ${count} 条练习记录）` : ''
  try {
    await ElMessageBox.confirm(
      `该词条已有练习记录${text}，为保证统计完整只能停用，不能删除。停用后保留记录但不再出题。`,
      '无法删除',
      { type: 'error', confirmButtonText: '改为停用', cancelButtonText: '取消' },
    )
    await patchWord(row.id, { status: 'inactive' })
    ElMessage.success('已改为停用')
    load()
  } catch {
    // 取消或失败
  }
}

function serial(index: number): number {
  return (page.value - 1) * pageSize.value + index + 1
}

onMounted(load)
</script>

<template>
  <div class="word-table">
    <div class="word-table__bar">
      <el-button :disabled="selected.length === 0" :loading="batching" type="success" @click="batchToggle('active')">
        批量启用
      </el-button>
      <el-button :disabled="selected.length === 0" :loading="batching" type="warning" @click="batchToggle('inactive')">
        批量停用
      </el-button>
      <span class="word-table__hint">已选 {{ selected.length }} 项</span>
    </div>

    <LoadingBox :loading="loading" :has-data="loaded" :rows="6">
      <EmptyState v-if="loaded && words.length === 0" description="该单元还没有词条，可用导入向导添加" />
      <template v-else>
        <el-table :data="words" border stripe @selection-change="onSelectionChange">
          <el-table-column type="selection" width="46" />
          <el-table-column label="序号" width="64" align="center">
            <template #default="{ $index }">{{ serial($index) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="80">
            <template #default="{ row }">{{ WORD_TYPE_LABEL[row.type] || row.type }}</template>
          </el-table-column>
          <el-table-column prop="en" label="英文" min-width="140" />
          <el-table-column prop="cn" label="中文" min-width="120" />
          <el-table-column label="可接受答案" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ (row.accepted || []).join('、') || '—' }}</template>
          </el-table-column>
          <el-table-column prop="phonetic" label="音标" min-width="120" show-overflow-tooltip />
          <el-table-column label="是否拼读" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.require_spell ? 'primary' : 'info'" size="small">
                {{ row.require_spell ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="removeWord(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            :current-page="page"
            :page-size="pageSize"
            :total="total"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            @current-change="onPageChange"
            @size-change="onSizeChange"
          />
        </div>
      </template>
    </LoadingBox>

    <WordEditDialog
      v-model:visible="editDialog.visible"
      :word="editDialog.word"
      @save="handleSave"
    />
  </div>
</template>

<style scoped>
.word-table__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.word-table__hint {
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0;
}
</style>
