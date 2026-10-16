<script setup lang="ts">
/**
 * 单元管理（两级树形）—— WEB-11 F14-1/F14-2/F14-3（todo11 B1）。
 *
 * - 树形展示：两级 el-tree（展开/收起），一级显示「N 个二级 · M 条」（M 为
 *   counts 汇总，后端已算好），二级显示条数；空态/加载态复用 PageHeader/
 *   LoadingBox/EmptyState 规范；
 * - 增删改：新建一级 / 在一级下新建二级（选父级）/ 改名与排序；删除确认弹层
 *   明示级联影响数字（一级：连带 N 个二级 + M 条内容；二级：M 条内容）；
 * - 移动归属：二级在一级之间移动（弹层下拉选目标一级）；后端校验失败由
 *   client.ts 统一弹 BizError 文案。
 *
 * 请求全部走 `api/units.ts`（v1.3 两级树 + Mock）；错误提示由 client.ts 统一
 * 弹一次，页面不重复弹。
 */
import { computed, onActivated, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { createUnit, deleteUnit, listUnits, updateUnit } from '@/api/units'
import type { UnitNode } from '@/api/units'

const loading = ref(false)
const loadedOnce = ref(false)
const units = ref<UnitNode[]>([])
const busy = ref(false)

/** 单元内容总数（一级为含二级的汇总值，后端契约保证）。 */
function totalOf(unit: UnitNode): number {
  return unit.counts.word + unit.counts.phrase + unit.counts.sentence
}

function isRoot(unit: UnitNode): boolean {
  return unit.parent_id === null
}

// ---------------------------------------------------------------------------
// 新建 / 编辑对话框
// ---------------------------------------------------------------------------

type DialogMode = 'create-root' | 'create-child' | 'edit'

const dialogVisible = ref(false)
const dialogMode = ref<DialogMode>('create-root')
/** create-child 时的父级；edit 时的目标。 */
const targetUnit = ref<UnitNode | null>(null)
const formRef = ref<FormInstance | null>(null)
const form = reactive<{ name: string; sort: number }>({ name: '', sort: 0 })

const dialogTitle = computed(() => {
  if (dialogMode.value === 'create-root') return '新建一级单元'
  if (dialogMode.value === 'create-child') return `新建二级单元（${targetUnit.value?.name ?? ''}）`
  return '编辑单元'
})

const rules: FormRules = {
  name: [{ required: true, message: '请输入单元名称', trigger: 'blur' }],
}

function openCreateRoot(): void {
  dialogMode.value = 'create-root'
  targetUnit.value = null
  form.name = ''
  form.sort = 0
  dialogVisible.value = true
}

function openCreateChild(parent: UnitNode): void {
  dialogMode.value = 'create-child'
  targetUnit.value = parent
  form.name = ''
  form.sort = 0
  dialogVisible.value = true
}

function openEdit(unit: UnitNode): void {
  dialogMode.value = 'edit'
  targetUnit.value = unit
  form.name = unit.name
  form.sort = unit.sort
  dialogVisible.value = true
}

async function submitForm(): Promise<void> {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  const name = form.name.trim()
  busy.value = true
  try {
    if (dialogMode.value === 'create-root') {
      await createUnit({ name, sort: form.sort })
      ElMessage.success('一级单元已创建')
    } else if (dialogMode.value === 'create-child') {
      await createUnit({ name, sort: form.sort, parent_id: targetUnit.value!.id })
      ElMessage.success('二级单元已创建')
    } else {
      await updateUnit(targetUnit.value!.id, { name, sort: form.sort })
      ElMessage.success('单元已更新')
    }
    dialogVisible.value = false
    await load()
  } catch {
    // 统一提示（client.ts 已弹）
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------------------
// 删除（确认文案必须含级联影响数字：一级连带 N 个二级 + M 条内容）
// ---------------------------------------------------------------------------

async function confirmDelete(unit: UnitNode): Promise<void> {
  const items = totalOf(unit)
  const text = isRoot(unit)
    ? `删除一级单元「${unit.name}」将连带删除其下 ${unit.children.length} 个二级单元、共 ${items} 条内容（含二级单元下的内容），且不可恢复。是否继续？`
    : `删除二级单元「${unit.name}」将同时删除其下全部 ${items} 条内容，且不可恢复。是否继续？`
  try {
    await ElMessageBox.confirm(text, '删除单元', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }

  busy.value = true
  try {
    await deleteUnit(unit.id)
    ElMessage.success('单元已删除')
    await load()
  } catch {
    // 统一提示（client.ts 已弹）
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------------------
// 移动归属（F14-3：二级在一级之间移动，下拉选目标一级）
// ---------------------------------------------------------------------------

const moveVisible = ref(false)
const moveChild = ref<UnitNode | null>(null)
const moveTargetId = ref<number | null>(null)

/** 目标一级候选 = 除当前父级外的全部一级单元。 */
const moveOptions = computed<UnitNode[]>(() => {
  const child = moveChild.value
  return units.value.filter((u) => u.id !== child?.parent_id)
})

function openMove(child: UnitNode): void {
  moveChild.value = child
  moveTargetId.value = null
  moveVisible.value = true
}

async function submitMove(): Promise<void> {
  const child = moveChild.value
  if (!child || moveTargetId.value == null) {
    ElMessage.warning('请选择目标一级单元')
    return
  }
  busy.value = true
  try {
    await updateUnit(child.id, { parent_id: moveTargetId.value })
    ElMessage.success(`「${child.name}」已移动归属`)
    moveVisible.value = false
    await load()
  } catch {
    // 后端校验失败（如挂到二级下）→ client.ts 统一展示错误文案
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
    // 统一提示（client.ts 已弹）
  } finally {
    loading.value = false
  }
}

/** 树的合计（头部提示用）。 */
const rootCount = computed(() => units.value.length)

// 页面在 AdminLayout 的 keep-alive 下：onActivated 首次进入也会触发，
// 保证从其他页面回来时树与 counts 最新
onActivated(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="单元管理" subtitle="两级单元树；删除一级会连带删除其下二级单元与全部内容">
      <el-button :disabled="busy" @click="load">刷新</el-button>
      <el-button type="primary" :disabled="busy" @click="openCreateRoot">新建一级单元</el-button>
    </PageHeader>

    <el-card shadow="never">
      <template #header>
        <div class="utree-card__header">
          <span>单元树</span>
          <span class="utree-card__hint">共 {{ rootCount }} 个一级单元</span>
        </div>
      </template>

      <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6">
        <EmptyState
          v-if="loadedOnce && units.length === 0"
          description="还没有单元，点击右上角「新建一级单元」开始"
        >
          <el-button type="primary" @click="openCreateRoot">新建一级单元</el-button>
        </EmptyState>

        <el-tree
          v-else
          :data="units"
          node-key="id"
          :props="{ label: 'name', children: 'children' }"
          :expand-on-click-node="false"
          default-expand-all
          v-loading="busy"
          class="utree"
        >
          <template #default="{ data }">
            <div class="utree__node">
              <span class="utree__name">{{ data.name }}</span>
              <span class="utree__meta">
                <template v-if="isRoot(data)">{{ data.children.length }} 个二级 · {{ totalOf(data) }} 条</template>
                <template v-else>{{ totalOf(data) }} 条</template>
              </span>
              <span class="utree__ops" @click.stop>
                <el-button
                  v-if="isRoot(data)"
                  link
                  type="primary"
                  size="small"
                  @click="openCreateChild(data)"
                >
                  添加二级
                </el-button>
                <el-button v-else link type="primary" size="small" @click="openMove(data)">移动</el-button>
                <el-button link type="primary" size="small" @click="openEdit(data)">编辑</el-button>
                <el-button link type="danger" size="small" @click="confirmDelete(data)">删除</el-button>
              </span>
            </div>
          </template>
        </el-tree>
      </LoadingBox>
    </el-card>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input
            v-model="form.name"
            maxlength="100"
            placeholder="一级自由录入，如 三年级上册 / Lesson 1"
          />
        </el-form-item>
        <el-form-item label="排序值">
          <el-input-number v-model="form.sort" :min="0" :max="9999" />
          <span class="utree__sort-hint">升序，越小越靠前</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>

    <!-- 移动归属（仅二级） -->
    <el-dialog v-model="moveVisible" title="移动归属" width="420px" :close-on-click-modal="false">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="二级单元">{{ moveChild?.name }}</el-form-item>
        <el-form-item label="目标一级">
          <el-select v-model="moveTargetId" placeholder="选择目标一级单元" style="width: 100%">
            <el-option
              v-for="opt in moveOptions"
              :key="opt.id"
              :label="opt.name"
              :value="opt.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="moveVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitMove">移动</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.utree-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.utree-card__hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}

.utree {
  background: transparent;
}

.utree :deep(.el-tree-node__content) {
  height: 40px;
}

/* 二级节点缩进后操作区容易挤，留足最小宽度 */
.utree__node {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  padding-right: 8px;
}

.utree__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.utree__meta {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--admin-text-secondary);
}

.utree__ops {
  margin-left: auto;
  flex: 0 0 auto;
}

.utree__sort-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--admin-text-secondary);
}
</style>
