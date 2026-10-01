<script setup lang="ts">
/**
 * 学生管理 —— WP-WEB-03 F2 / F4 / F5 / F6 / F14
 *
 * F2：列表（学号/姓名/班级/来源/状态/最后登录/练习数/正确率）+ 筛选（班级含「未分班」/状态/来源/关键词）。
 * F4：单个归班（行内操作弹窗）+ 批量归班（勾选后选班级）；归班后可筛选「未分班」验证。
 * F5：重置密码（弹窗，成功后提示需告知学生）。
 * F6：学生停用（二次确认，提示「停用后该学生无法登录」）。
 * F14：空状态引导 + 主操作按钮。
 * 共通约束：危险操作二次确认（#4）；表格三态（#5）；未分班为显式筛选项（规格关键交互）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import ResetPasswordDialog from './ResetPasswordDialog.vue'
import {
  assignClass,
  batchAssignClass,
  listClassOptions,
  listStudents,
  patchStudent,
  type RegisterSource,
  type StudentItem,
  type UserStatus,
} from '@/api/students'
import { formatDateTime, formatPercent } from '@/utils/format'

const router = useRouter()

const SOURCE_LABEL: Record<RegisterSource, string> = { import: '导入', self: '自主注册' }
const STATUS_LABEL: Record<UserStatus, string> = { active: '正常', disabled: '停用' }

// ----------------------------- 班级下拉数据源
const classOptions = ref<{ label: string; value: number }[]>([])
async function loadClassOptions(): Promise<void> {
  try {
    const res = await listClassOptions({ status: 'all' })
    classOptions.value = res.items.map((c) => ({ label: c.name, value: c.id }))
  } catch {
    // 统一提示
  }
}

// ----------------------------- 筛选
const filters = reactive<{
  classId: number | 'unassigned' | ''
  status: UserStatus | ''
  source: RegisterSource | ''
  keyword: string
}>({ classId: '', status: '', source: '', keyword: '' })

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const unassignedCount = ref(0)
const loading = ref(false)
const students = ref<StudentItem[]>([])
const loadedOnce = ref(false)

function buildQuery() {
  const q: { classId?: number | null; status?: UserStatus | ''; registerSource?: RegisterSource | ''; keyword?: string; page: number; pageSize: number } = {
    page: page.value,
    pageSize: pageSize.value,
  }
  if (filters.classId === 'unassigned') q.classId = null
  else if (typeof filters.classId === 'number') q.classId = filters.classId
  if (filters.status) q.status = filters.status
  if (filters.source) q.registerSource = filters.source
  if (filters.keyword.trim()) q.keyword = filters.keyword.trim()
  return q
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await listStudents(buildQuery())
    students.value = res.items
    total.value = res.total
    unassignedCount.value = res.unassigned_count
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  load()
}

function onReset(): void {
  filters.classId = ''
  filters.status = ''
  filters.source = ''
  filters.keyword = ''
  page.value = 1
  load()
}

function onPageChange(p: number): void {
  page.value = p
  load()
}

function onSizeChange(s: number): void {
  pageSize.value = s
  page.value = 1
  load()
}

// ----------------------------- 单个归班
const assignDialogVisible = ref(false)
const assignTargetClass = ref<number | ''>('')
const assignIsBatch = ref(false)
const assignStudent = ref<StudentItem | null>(null)
const assigning = ref(false)
const batchIds = ref<number[]>([])
const batchAssigning = ref(false)

function openAssign(row: StudentItem | null): void {
  assignStudent.value = row
  assignIsBatch.value = !row
  assignTargetClass.value = row?.class_id ?? ''
  assignDialogVisible.value = true
}

async function submitAssign(): Promise<void> {
  const classId = assignTargetClass.value === '' ? null : assignTargetClass.value
  assigning.value = true
  try {
    if (assignIsBatch.value) {
      batchAssigning.value = true
      const res = await batchAssignClass(batchIds.value, classId)
      ElMessage.success(
        `批量归班完成：成功 ${res.success_count} 人${res.failed_count ? `，失败 ${res.failed_count} 人` : ''}`,
      )
    } else if (assignStudent.value) {
      const verb = classId === null ? '移出班级' : '归班'
      await assignClass(assignStudent.value.id, classId)
      ElMessage.success(`已${verb}`)
    }
    assignDialogVisible.value = false
    load()
  } catch {
    // 统一提示
  } finally {
    assigning.value = false
    batchAssigning.value = false
  }
}

// ----------------------------- 重置密码
const resetDialogVisible = ref(false)
const resetStudent = ref<StudentItem | null>(null)
function openReset(row: StudentItem): void {
  resetStudent.value = row
  resetDialogVisible.value = true
}

// ----------------------------- 停用 / 启用（F6：二次确认）
async function toggleStatus(row: StudentItem): Promise<void> {
  const next: UserStatus = row.status === 'active' ? 'disabled' : 'active'
  const verb = next === 'disabled' ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(
      next === 'disabled'
        ? `停用后该学生无法登录，但保留全部答题记录与统计。确定停用「${row.real_name}」？`
        : `确定重新启用「${row.real_name}」？`,
      `确认${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await patchStudent(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    load()
  } catch {
    // 统一提示
  }
}

// ----------------------------- 表格选择
function onSelectionChange(rows: StudentItem[]): void {
  batchIds.value = rows.map((r) => r.id)
}

function goReport(row: StudentItem): void {
  router.push(`/stats/student?student_id=${row.id}`)
}

const sourceOptions = computed(() =>
  (['', 'import', 'self'] as const).map((v) => ({
    label: v === '' ? '全部来源' : SOURCE_LABEL[v],
    value: v,
  })),
)
const statusOptions = computed(() =>
  (['', 'active', 'disabled'] as const).map((v) => ({
    label: v === '' ? '全部状态' : STATUS_LABEL[v],
    value: v,
  })),
)
function statusType(s: UserStatus): 'success' | 'info' {
  return s === 'active' ? 'success' : 'info'
}

onMounted(() => {
  loadClassOptions()
  load()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="学生管理"
      :subtitle="loadedOnce ? `共 ${total} 人 · 未分班 ${unassignedCount} 人` : '学生管理'"
    >
      <el-button type="primary" @click="router.push('/students/import')">导入学生</el-button>
      <el-button
        :disabled="batchIds.length === 0"
        @click="openAssign(null)"
      >
        批量归班（{{ batchIds.length }}）
      </el-button>
    </PageHeader>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="班级">
          <el-select v-model="filters.classId" placeholder="全部" clearable style="width: 160px">
            <el-option label="全部班级" value="" />
            <el-option label="未分班" value="unassigned" />
            <el-option v-for="c in classOptions" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" style="width: 120px">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="filters.source" placeholder="全部" style="width: 130px">
            <el-option v-for="o in sourceOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" placeholder="学号或姓名" clearable style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6" class="table-wrap">
      <EmptyState
        v-if="loadedOnce && students.length === 0"
        description="没有符合条件的学生"
      >
        <el-button type="primary" @click="router.push('/students/import')">导入学生</el-button>
      </EmptyState>

      <template v-else>
        <el-table :data="students" border stripe @selection-change="onSelectionChange">
          <el-table-column type="selection" width="44" />
          <el-table-column prop="username" label="学号" width="130" />
          <el-table-column prop="real_name" label="姓名" width="110" />
          <el-table-column label="班级" min-width="130">
            <template #default="{ row }">
              <span>{{ row.class_name || '未分班' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="100" align="center">
            <template #default="{ row }">{{ SOURCE_LABEL[row.register_source as RegisterSource] }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="statusType(row.status)" size="small">{{ STATUS_LABEL[row.status as UserStatus] }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最后登录" width="140">
            <template #default="{ row }">{{ formatDateTime(row.last_login_at) }}</template>
          </el-table-column>
          <el-table-column prop="practice_count" label="练习数" width="90" align="center" />
          <el-table-column label="正确率" width="100" align="center">
            <template #default="{ row }">
              {{ row.practice_count ? formatPercent(row.accuracy, true) : '—' }}
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="240" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="goReport(row)">报告</el-button>
              <el-button link type="primary" @click="openAssign(row)">归班</el-button>
              <el-button link type="primary" @click="openReset(row)">重置密码</el-button>
              <el-button link type="warning" @click="toggleStatus(row)">
                {{ row.status === 'active' ? '停用' : '启用' }}
              </el-button>
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

    <!-- 单个 / 批量 归班弹窗 -->
    <el-dialog
      v-model="assignDialogVisible"
      :title="assignIsBatch ? `批量归班（${batchIds.length} 人）` : '调整班级'"
      width="420px"
    >
      <el-alert
        v-if="assignIsBatch"
        type="info"
        :closable="false"
        title="批量归班：部分失败不回滚整批，完成后会在结果中告知成功/失败人数。"
        style="margin-bottom: 12px"
      />
      <el-form label-width="80px">
        <el-form-item :label="assignIsBatch ? '目标班级' : '班级'">
          <el-select v-model="assignTargetClass" placeholder="选择班级（空=移出班级）" clearable style="width: 100%">
            <el-option label="（移出班级 / 未分班）" value="" />
            <el-option v-for="c in classOptions" :key="c.value" :label="c.label" :value="c.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="assigning" @click="submitAssign">确认</el-button>
      </template>
    </el-dialog>

    <ResetPasswordDialog v-model="resetDialogVisible" :student="resetStudent" />
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 16px;
}
.table-wrap {
  background: var(--admin-panel);
}
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0;
}
</style>
