<script setup lang="ts">
/**
 * 单元列表 —— WP-WEB-02 F1/F2/F3
 *
 * F1 筛选（学科/年级/学期/状态）+ 分页；列显示词条数/课文数/启用数。
 * F2 新建/编辑：学科/年级(1-9)/学期/名称必填；重名前端先查当前列表提示，后端 10001 兜底。
 * F3 删除：后端 30003 → 弹窗说明「已有练习记录只能停用」并提供「改为停用」按钮。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/content/StatusTag.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import {
  createUnit,
  deleteUnit,
  listUnits,
  updateUnit,
  type CreateUnitPayload,
  type Subject,
  type Semester,
  type UnitItem,
  type UnitListQuery,
} from '@/api/content'
import { BizError } from '@/api/client'

const router = useRouter()

const SUBJECT_LABEL: Record<Subject, string> = { english: '英语', chinese: '语文' }
const SEMESTER_LABEL: Record<Semester, string> = { first: '上学期', second: '下学期' }

// ----------------------------- 筛选 + 分页
const filters = reactive<{
  subject: Subject | ''
  grade: number | ''
  semester: Semester | ''
  status: 'active' | 'inactive' | ''
}>({ subject: '', grade: '', semester: '', status: '' })

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const units = ref<UnitItem[]>([])
const loadedOnce = ref(false)

function buildQuery(): UnitListQuery {
  const q: UnitListQuery = { page: page.value, page_size: pageSize.value }
  if (filters.subject) q.subject = filters.subject
  if (filters.grade !== '') q.grade = filters.grade
  if (filters.semester) q.semester = filters.semester
  if (filters.status) q.status = filters.status
  return q
}

async function load() {
  loading.value = true
  try {
    const res = await listUnits(buildQuery())
    units.value = res.items
    total.value = res.total
    loadedOnce.value = true
  } catch {
    // client 已统一弹提示
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  load()
}

function onReset() {
  filters.subject = ''
  filters.grade = ''
  filters.semester = ''
  filters.status = ''
  page.value = 1
  load()
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

// ----------------------------- 新建 / 编辑
const dialogVisible = ref(false)
const editing = ref<UnitItem | null>(null)
const saving = ref(false)
const formRef = ref()
const form = reactive<CreateUnitPayload & { id?: number }>({
  id: undefined,
  subject: 'english',
  grade: 3,
  semester: 'first',
  name: '',
  order_no: 0,
  remark: '',
})

const rules = {
  subject: [{ required: true, message: '请选择学科', trigger: 'change' }],
  grade: [
    { required: true, message: '请选择年级', trigger: 'change' },
    { type: 'number', min: 1, max: 9, message: '年级为 1–9', trigger: 'change' },
  ],
  semester: [{ required: true, message: '请选择学期', trigger: 'change' }],
  name: [{ required: true, message: '请输入单元名称', trigger: 'blur' }],
}

function openCreate() {
  editing.value = null
  form.id = undefined
  form.subject = 'english'
  form.grade = 3
  form.semester = 'first'
  form.name = ''
  form.order_no = 0
  form.remark = ''
  dialogVisible.value = true
}

function openEdit(row: UnitItem) {
  editing.value = row
  form.id = row.id
  form.subject = row.subject
  form.grade = row.grade
  form.semester = row.semester
  form.name = row.name
  form.order_no = row.order_no
  form.remark = row.remark ?? ''
  dialogVisible.value = true
}

/** F2：重名前端先提示（按当前列表查同 学科+年级+学期+名称；新名称提交仍以后端 10001 为准）。 */
function nameConflict(): boolean {
  const key = (u: UnitItem) =>
    `${u.subject}|${u.grade}|${u.semester}|${u.name.trim().toLowerCase()}`
  const target = `${form.subject}|${form.grade}|${form.semester}|${form.name.trim().toLowerCase()}`
  return units.value.some((u) => u.id !== form.id && key(u) === target)
}

async function submitForm() {
  await formRef.value.validate()
  if (nameConflict()) {
    ElMessage.warning('当前筛选结果中已存在同名同年级同学科的单元，请确认是否重复')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await updateUnit(editing.value.id, {
        name: form.name,
        order_no: form.order_no,
        remark: form.remark || null,
      })
      ElMessage.success('已保存')
    } else {
      await createUnit({
        subject: form.subject,
        grade: form.grade,
        semester: form.semester,
        name: form.name,
        order_no: form.order_no,
        remark: form.remark || null,
      })
      ElMessage.success('单元已创建')
    }
    dialogVisible.value = false
    load()
  } catch {
    // 10001 等由 client 统一提示
  } finally {
    saving.value = false
  }
}

// ----------------------------- 停用 / 启用
async function toggleStatus(row: UnitItem) {
  const next = row.status === 'active' ? 'inactive' : 'active'
  const verb = next === 'inactive' ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(
      next === 'inactive'
        ? '停用后该单元不再出题，但保留全部记录与历史成绩。确定停用？'
        : '确定启用该单元？',
      `确认${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await updateUnit(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    load()
  } catch {
    // 统一提示
  }
}

// ----------------------------- 删除（F3：30003 特殊处理）
async function removeUnit(row: UnitItem) {
  let confirmed = false
  try {
    await ElMessageBox.confirm(
      '删除会彻底移除该单元及其下的词条/课文（不可恢复）。确定删除？',
      '确认删除',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
    confirmed = true
  } catch {
    return
  }
  if (!confirmed) return
  try {
    await deleteUnit(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    if (e instanceof BizError && e.code === 30003) {
      await showHasRecordsDialog(row, (e.data as { record_count?: number } | null)?.record_count)
    }
    // 其它错误 client 已弹
  }
}

/** F3：有练习记录不可删 —— 弹窗说明并提供「改为停用」。 */
async function showHasRecordsDialog(row: UnitItem, recordCount?: number) {
  const countText = recordCount !== undefined ? `（共 ${recordCount} 条练习记录）` : ''
  try {
    await ElMessageBox.confirm(
      `该单元已有练习记录${countText}，为保证统计完整只能停用，不能删除。\n停用后不再出题，但保留全部记录与历史成绩。`,
      '无法删除',
      {
        type: 'error',
        confirmButtonText: '改为停用',
        cancelButtonText: '取消',
        showCancelButton: true,
      },
    )
    await updateUnit(row.id, { status: 'inactive' })
    ElMessage.success('已改为停用')
    load()
  } catch {
    // 用户取消或失败
  }
}

function goDetail(row: UnitItem) {
  router.push(`/content/units/${row.id}`)
}

const statusFilterOptions = [
  { label: '全部', value: '' },
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
]

const subjectOptions = computed(() =>
  (['', 'english', 'chinese'] as const).map((v) => ({
    label: v === '' ? '全部学科' : SUBJECT_LABEL[v],
    value: v,
  })),
)
const semesterOptions = computed(() =>
  (['', 'first', 'second'] as const).map((v) => ({
    label: v === '' ? '全部学期' : SEMESTER_LABEL[v],
    value: v,
  })),
)
const gradeOptions = computed(() =>
  ([1, 2, 3, 4, 5, 6, 7, 8, 9] as const).map((g) => ({ label: `${g} 年级`, value: g })),
)

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="内容管理" subtitle="管理单元、词条与课文">
      <el-button type="primary" @click="openCreate">新建单元</el-button>
      <el-button @click="router.push('/content/import')">导入向导</el-button>
    </PageHeader>

    <!-- F1 筛选 -->
    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="学科">
          <el-select v-model="filters.subject" placeholder="全部" clearable style="width: 130px">
            <el-option v-for="o in subjectOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="年级">
          <el-select v-model="filters.grade" placeholder="全部" clearable style="width: 120px">
            <el-option v-for="o in gradeOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="学期">
          <el-select v-model="filters.semester" placeholder="全部" clearable style="width: 130px">
            <el-option v-for="o in semesterOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" style="width: 120px">
            <el-option v-for="o in statusFilterOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="onSearch">查询</el-button>
          <el-button @click="onReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6" class="table-wrap">
      <EmptyState v-if="loadedOnce && units.length === 0" description="没有符合条件的单元">
        <el-button type="primary" @click="openCreate">新建单元</el-button>
      </EmptyState>

      <el-table v-else :data="units" border stripe @row-dblclick="goDetail">
        <el-table-column prop="id" label="ID" width="72" />
        <el-table-column label="单元名称" min-width="160">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="goDetail(row)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column label="学科" width="90">
          <template #default="{ row }">{{ SUBJECT_LABEL[row.subject as Subject] }}</template>
        </el-table-column>
        <el-table-column label="年级" width="80">
          <template #default="{ row }">{{ row.grade }} 年级</template>
        </el-table-column>
        <el-table-column label="学期" width="90">
          <template #default="{ row }">{{ SEMESTER_LABEL[row.semester as Semester] }}</template>
        </el-table-column>
        <el-table-column label="词条数" width="90" align="center">
          <template #default="{ row }">{{ row.word_count }}</template>
        </el-table-column>
        <el-table-column label="课文数" width="90" align="center">
          <template #default="{ row }">{{ row.text_count }}</template>
        </el-table-column>
        <el-table-column label="启用数" width="100" align="center">
          <template #default="{ row }">
            <span>{{ row.active_word_count }}/{{ row.word_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }"><StatusTag :status="row.status" /></template>
        </el-table-column>
        <el-table-column label="操作" min-width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="goDetail(row)">详情</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" @click="toggleStatus(row)">
              {{ row.status === 'active' ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" @click="removeUnit(row)">删除</el-button>
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

    <!-- F2 新建 / 编辑 -->
    <el-dialog v-model="dialogVisible" :title="editing ? '编辑单元' : '新建单元'" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="学科" prop="subject">
          <el-select v-model="form.subject" :disabled="!!editing" style="width: 100%">
            <el-option label="英语" value="english" />
            <el-option label="语文" value="chinese" />
          </el-select>
        </el-form-item>
        <el-form-item label="年级" prop="grade">
          <el-select v-model="form.grade" :disabled="!!editing" style="width: 100%">
            <el-option v-for="g in gradeOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="学期" prop="semester">
          <el-select v-model="form.semester" :disabled="!!editing" style="width: 100%">
            <el-option label="上学期" value="first" />
            <el-option label="下学期" value="second" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 Unit 1 Hello!" />
        </el-form-item>
        <el-form-item label="序号">
          <el-input-number v-model="form.order_no" :min="0" :max="9999" />
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
