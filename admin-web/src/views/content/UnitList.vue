<script setup lang="ts">
/**
 * 分类页（单元管理）—— WP-B 功能点 1-3。
 *
 * - 列表展示全部单元：名称、排序值、三类内容数量（来自 `GET /api/units` 的 counts）；
 * - 新建（name 必填、sort 默认 0）/ 编辑改名与排序 / 删除（确认文案明确
 *   「将同时删除单元下全部单词/词组/短句」——验收 U3）；
 * - 无学科/年级/学期任何选择器（这些概念已删除，出现即缺陷）。
 *
 * 请求全部走 `api/units.ts`；错误提示由 client.ts 统一弹一次，页面不重复弹。
 */
import { computed, onActivated, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import UnitTree from '@/components/UnitTree.vue'
import { createUnit, deleteUnit, listUnits, updateUnit } from '@/api/units'
import { ITEM_TYPE_LABEL, ITEM_TYPES } from '@/api/items'
import type { ItemType, UnitNode } from '@/api/types'

const loading = ref(false)
const loadedOnce = ref(false)
const units = ref<UnitNode[]>([])

/** 排序后展示（后端已按 sort,id 排，这里双保险再排一次，纯展示不回写）。 */
const sortedUnits = computed(() => [...units.value].sort((a, b) => a.sort - b.sort || a.id - b.id))

const busy = ref(false)

// ---------------------------------------------------------------------------
// 新建 / 编辑对话框
// ---------------------------------------------------------------------------

const dialogVisible = ref(false)
/** 编辑目标；null = 新建。 */
const editing = ref<UnitNode | null>(null)
const formRef = ref<FormInstance | null>(null)
const form = reactive<{ name: string; sort: number }>({ name: '', sort: 0 })

const rules: FormRules = {
  name: [{ required: true, message: '请输入单元名称', trigger: 'blur' }],
}

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.sort = 0
  dialogVisible.value = true
}

function openEdit(unit: UnitNode): void {
  editing.value = unit
  form.name = unit.name
  form.sort = unit.sort
  dialogVisible.value = true
}

async function submitForm(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  busy.value = true
  try {
    if (editing.value) {
      await updateUnit(editing.value.id, { name: form.name.trim(), sort: form.sort })
      ElMessage.success('单元已更新')
    } else {
      await createUnit({ name: form.name.trim(), sort: form.sort })
      ElMessage.success('单元已创建')
    }
    dialogVisible.value = false
    await load()
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------------------
// 删除（U3：确认文案必须含「将同时删除单元下全部单词/词组/短句」）
// ---------------------------------------------------------------------------

async function confirmDelete(unit: UnitNode): Promise<void> {
  const total = unit.counts.word + unit.counts.phrase + unit.counts.sentence
  const extra = total > 0 ? `（该单元下共有 ${total} 条内容）` : ''
  try {
    await ElMessageBox.confirm(
      `删除单元「${unit.name}」${extra}，将同时删除单元下全部单词/词组/短句，且不可恢复。是否继续？`,
      '删除单元',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }

  busy.value = true
  try {
    await deleteUnit(unit.id)
    ElMessage.success('单元已删除')
    await load()
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

async function load(): Promise<void> {
  loading.value = true
  try {
    units.value = await listUnits()
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

/** 类型列头顺序（word/phrase/sentence）。 */
const typeColumns: ItemType[] = [...ITEM_TYPES]

// 页面在 AdminLayout 的 keep-alive 下：用 onActivated（首次进入也会触发），
// 这样在「内容录入」页增删内容后切回来，counts 仍是最新
onActivated(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="分类" subtitle="教材单元管理；删除单元会连带删除其下全部内容">
      <el-button type="primary" @click="openCreate">新建单元</el-button>
    </PageHeader>

    <el-row :gutter="16">
      <!-- 左：单元树（共用组件，选中仅作预览，本页操作走表格） -->
      <el-col :span="7">
        <el-card shadow="never" class="unit-list__tree-card">
          <template #header>
            <div class="unit-list__card-header">
              <span>单元树</span>
              <span class="unit-list__card-hint">共 {{ units.length }} 个单元</span>
            </div>
          </template>
          <UnitTree />
        </el-card>
      </el-col>

      <!-- 右：单元列表 -->
      <el-col :span="17">
        <el-card shadow="never" class="unit-list__table-card">
          <template #header>
            <div class="unit-list__card-header">
              <span>单元列表</span>
              <span class="unit-list__card-hint">按排序值升序展示</span>
            </div>
          </template>

          <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6">
            <EmptyState v-if="loadedOnce && units.length === 0" description="还没有单元，点击右上角「新建单元」开始">
              <el-button type="primary" @click="openCreate">新建单元</el-button>
            </EmptyState>

            <el-table v-else :data="sortedUnits" border stripe size="small" v-loading="busy">
              <el-table-column prop="name" label="名称" min-width="180" />
              <el-table-column prop="sort" label="排序值" width="90" align="center" />
              <el-table-column
                v-for="t in typeColumns"
                :key="t"
                :label="ITEM_TYPE_LABEL[t]"
                width="80"
                align="center"
              >
                <template #default="{ row }">{{ row.counts[t] }}</template>
              </el-table-column>
              <el-table-column label="操作" width="140" align="center">
                <template #default="{ row }">
                  <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                  <el-button link type="danger" size="small" @click="confirmDelete(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </LoadingBox>
        </el-card>
      </el-col>
    </el-row>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editing ? '编辑单元' : '新建单元'"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="100" placeholder="如 Starter Unit 1" />
        </el-form-item>
        <el-form-item label="排序值">
          <el-input-number v-model="form.sort" :min="0" :max="9999" />
          <span class="unit-list__sort-hint">升序，越小越靠前</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.unit-list__tree-card,
.unit-list__table-card {
  margin-bottom: 16px;
}

.unit-list__card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.unit-list__card-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}

.unit-list__sort-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--admin-text-secondary);
}
</style>
