<script setup lang="ts">
/**
 * 课文表格 —— WP-WEB-02 F9
 *
 * F9 列：标题/作者/总字数/背诵字数/段落数/状态。
 * F4 分页：后端 `/api/units/{id}/texts` 不分页（一次返回单元下全部课文），
 * 故此处做**客户端分页**（仍是 UI 层分页，满足 F4；不修改后端）。
 * 删除（共通约束 4 要求二次确认）：有背诵记录 → 后端 30003 → 提示记录数并建议停用。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import StatusTag from '@/components/content/StatusTag.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { deleteText, listUnitTexts, patchText, type TextBrief } from '@/api/content'
import { BizError } from '@/api/client'

const props = defineProps<{ unitId: number }>()

const loading = ref(false)
const loaded = ref(false)
const all = ref<TextBrief[]>([])
const page = ref(1)
const pageSize = ref(20)

// 客户端分页切片
const paged = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return all.value.slice(start, start + pageSize.value)
})

async function load() {
  loading.value = true
  try {
    const res = await listUnitTexts(props.unitId)
    all.value = res.texts
    loaded.value = true
  } catch {
    // client 统一提示
  } finally {
    loading.value = false
  }
}

function onSizeChange() {
  page.value = 1
}

// unitId 变化（切换单元）时重新拉取
watch(
  () => props.unitId,
  () => {
    page.value = 1
    load()
  },
)

const toggleingId = ref<number | null>(null)
async function toggleStatus(row: TextBrief) {
  const next = row.status === 'active' ? 'inactive' : 'active'
  const verb = next === 'inactive' ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(
      next === 'inactive'
        ? '停用后该课文不再用于背诵，但保留全部记录。确定停用？'
        : '确定启用该课文？',
      `确认${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  toggleingId.value = row.id
  try {
    await patchText(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    load()
  } catch {
    // 统一提示
  } finally {
    toggleingId.value = null
  }
}

async function removeText(row: TextBrief) {
  try {
    await ElMessageBox.confirm(
      '删除会彻底移除该课文（不可恢复）。确定删除？',
      '确认删除',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteText(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e instanceof BizError && e.code === 30003) {
      const count = (e.data as { record_count?: number } | null)?.record_count
      await showHasRecords(row, count)
    }
  }
}

async function showHasRecords(row: TextBrief, count?: number) {
  const text = count !== undefined ? `（共 ${count} 条背诵记录）` : ''
  try {
    await ElMessageBox.confirm(
      `该课文已有背诵记录${text}，为保证统计完整只能停用，不能删除。停用后保留记录但不再用于背诵。`,
      '无法删除',
      { type: 'error', confirmButtonText: '改为停用', cancelButtonText: '取消' },
    )
    await patchText(row.id, { status: 'inactive' })
    ElMessage.success('已改为停用')
    load()
  } catch {
    // 取消或失败
  }
}

onMounted(load)
</script>

<template>
  <div class="text-table">
    <LoadingBox :loading="loading" :has-data="loaded" :rows="6">
      <EmptyState v-if="loaded && all.length === 0" description="该单元还没有课文，可用导入向导添加" />
      <template v-else>
        <el-table :data="paged" border stripe>
          <el-table-column prop="title" label="标题" min-width="180" />
          <el-table-column prop="author" label="作者" width="120">
            <template #default="{ row }">{{ row.author || '—' }}</template>
          </el-table-column>
          <el-table-column label="总字数" width="100" align="center">
            <template #default="{ row }">{{ row.total_chars }}</template>
          </el-table-column>
          <el-table-column label="背诵字数" width="100" align="center">
            <template #default="{ row }">{{ row.recite_scope_chars }}</template>
          </el-table-column>
          <el-table-column label="段落数" width="90" align="center">
            <template #default="{ row }">{{ row.paragraph_count }}</template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }"><StatusTag :status="row.status" /></template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :loading="toggleingId === row.id" @click="toggleStatus(row)">
                {{ row.status === 'active' ? '停用' : '启用' }}
              </el-button>
              <el-button link type="danger" @click="removeText(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            v-model:current-page="page"
            :page-size="pageSize"
            :total="all.length"
            :page-sizes="[20, 50, 100]"
            layout="total, sizes, prev, pager, next"
            @size-change="onSizeChange"
          />
        </div>
      </template>
    </LoadingBox>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0;
}
</style>
