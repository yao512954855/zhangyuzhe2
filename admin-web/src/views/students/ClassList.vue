<script setup lang="ts">
/**
 * 班级管理 —— WP-WEB-03 F1
 *
 * F1：列表（含学生数 / 未分班数）+ 新建；筛选 年级 / 学期 / 状态。
 * 危险操作（归档）二次确认，文案说清后果（共通约束 #4）。
 * 表格空 / 加载中 / 错误三态齐全（共通约束 #5）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import {
  createClass,
  listClasses,
  updateClass,
  exportClassCredentials,
  type ClassItem,
  type ClassListQuery,
  type ClassStatus,
  type CreateClassPayload,
  type Semester,
} from '@/api/students'

const SEMESTER_LABEL: Record<Semester, string> = { first: '上学期', second: '下学期' }
const CLASS_STATUS_LABEL: Record<ClassStatus, string> = { active: '正常', archived: '已归档' }

const filters = reactive<{
  grade: number | ''
  semester: Semester | ''
  status: ClassStatus | 'all' | ''
  keyword: string
}>({ grade: '', semester: '', status: 'all', keyword: '' })

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const unassignedCount = ref(0)
const loading = ref(false)
const classes = ref<ClassItem[]>([])
const loadedOnce = ref(false)

function buildQuery(): ClassListQuery {
  const q: ClassListQuery = { page: page.value, pageSize: pageSize.value }
  if (filters.grade !== '') q.grade = filters.grade
  if (filters.semester) q.semester = filters.semester
  if (filters.status) q.status = filters.status
  if (filters.keyword.trim()) q.keyword = filters.keyword.trim()
  return q
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await listClasses(buildQuery())
    classes.value = res.items
    total.value = res.total
    unassignedCount.value = res.unassigned_count
    loadedOnce.value = true
  } catch {
    // client 已统一弹提示
  } finally {
    loading.value = false
  }
}

function onSearch(): void {
  page.value = 1
  load()
}

function onReset(): void {
  filters.grade = ''
  filters.semester = ''
  filters.status = 'all'
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

// ----------------------------- 新建
const dialogVisible = ref(false)
const saving = ref(false)
const formRef = ref()
const form = reactive<CreateClassPayload & { id?: number }>({
  id: undefined,
  name: '',
  grade: 3,
  semester: 'first',
  school_year: '',
  remark: '',
})

const rules = {
  name: [{ required: true, message: '请输入班级名称', trigger: 'blur' }],
  grade: [
    { required: true, message: '请选择年级', trigger: 'change' },
    { type: 'number', min: 1, max: 9, message: '年级为 1–9', trigger: 'change' },
  ],
  semester: [{ required: true, message: '请选择学期', trigger: 'change' }],
}

function openCreate(): void {
  form.id = undefined
  form.name = ''
  form.grade = 3
  form.semester = 'first'
  form.school_year = ''
  form.remark = ''
  dialogVisible.value = true
}

const gradeOptions = computed(() =>
  ([1, 2, 3, 4, 5, 6, 7, 8, 9] as const).map((g) => ({ label: `${g} 年级`, value: g })),
)

async function submitForm(): Promise<void> {
  await formRef.value.validate()
  saving.value = true
  try {
    await createClass({
      name: form.name.trim(),
      grade: form.grade,
      semester: form.semester,
      school_year: form.school_year?.trim() || null,
      remark: form.remark?.trim() || null,
    })
    ElMessage.success('班级已创建')
    dialogVisible.value = false
    load()
  } catch {
    // 10001 重名等由 client 统一提示
  } finally {
    saving.value = false
  }
}

// ----------------------------- 归档 / 启用
async function toggleStatus(row: ClassItem): Promise<void> {
  const next: ClassStatus = row.status === 'active' ? 'archived' : 'active'
  const verb = next === 'archived' ? '归档' : '启用'
  try {
    await ElMessageBox.confirm(
      next === 'archived'
        ? '归档后该班级不再出现在默认列表（可在筛选中选「已归档」查看），但保留全部学生与记录。确定归档？'
        : '确定重新启用该班级？',
      `确认${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await updateClass(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    load()
  } catch {
    // 统一提示
  }
}

// ----------------------------- 导出账号对照表（无密码名单）
async function exportRoster(row: ClassItem): Promise<void> {
  try {
    const blob = await exportClassCredentials(row.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.name}_学生名单.xlsx`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出学生名单')
  } catch {
    // 统一提示
  }
}

const semesterOptions = computed(() =>
  (['', 'first', 'second'] as const).map((v) => ({
    label: v === '' ? '全部学期' : SEMESTER_LABEL[v],
    value: v,
  })),
)
const statusOptions = computed(() =>
  (['all', 'active', 'archived'] as const).map((v) => ({
    label: v === 'all' ? '全部状态' : CLASS_STATUS_LABEL[v],
    value: v,
  })),
)

function statusType(status: ClassStatus): 'success' | 'info' {
  return status === 'active' ? 'success' : 'info'
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="班级管理"
      :subtitle="loadedOnce ? `共 ${total} 个班级 · 未分班 ${unassignedCount} 人` : '班级管理'"
    >
      <el-button type="primary" @click="openCreate">新建班级</el-button>
    </PageHeader>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="年级">
          <el-select v-model="filters.grade" placeholder="全部" clearable style="width: 120px">
            <el-option v-for="g in gradeOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="学期">
          <el-select v-model="filters.semester" placeholder="全部" clearable style="width: 130px">
            <el-option v-for="o in semesterOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" style="width: 130px">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词">
          <el-input v-model="filters.keyword" placeholder="班级名称" clearable style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6" class="table-wrap">
      <EmptyState
        v-if="loadedOnce && classes.length === 0"
        description="没有符合条件的班级"
      >
        <el-button type="primary" @click="openCreate">新建班级</el-button>
      </EmptyState>

      <el-table v-else :data="classes" border stripe>
        <el-table-column prop="id" label="ID" width="72" />
        <el-table-column prop="name" label="班级名称" min-width="160" />
        <el-table-column label="年级" width="80">
          <template #default="{ row }">{{ row.grade }} 年级</template>
        </el-table-column>
        <el-table-column label="学期" width="90">
          <template #default="{ row }">{{ SEMESTER_LABEL[row.semester as Semester] }}</template>
        </el-table-column>
        <el-table-column prop="school_year" label="学年" width="120">
          <template #default="{ row }">{{ row.school_year || '-' }}</template>
        </el-table-column>
        <el-table-column label="学生数" width="90" align="center">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="exportRoster(row)">{{ row.student_count }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ CLASS_STATUS_LABEL[row.status as ClassStatus] }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="exportRoster(row)">导出名单</el-button>
            <el-button link type="warning" @click="toggleStatus(row)">
              {{ row.status === 'active' ? '归档' : '启用' }}
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
    </LoadingBox>

    <el-dialog v-model="dialogVisible" title="新建班级" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="班级名称" prop="name">
          <el-input v-model="form.name" placeholder="如 三年级(1)班" />
        </el-form-item>
        <el-form-item label="年级" prop="grade">
          <el-select v-model="form.grade" style="width: 100%">
            <el-option v-for="g in gradeOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="学期" prop="semester">
          <el-select v-model="form.semester" style="width: 100%">
            <el-option label="上学期" value="first" />
            <el-option label="下学期" value="second" />
          </el-select>
        </el-form-item>
        <el-form-item label="学年">
          <el-input v-model="form.school_year" placeholder="如 2026-2027（可选）" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
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
