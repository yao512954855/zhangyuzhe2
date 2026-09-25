<script setup lang="ts">
/**
 * 作业布置 —— WP-2G（二期 G2），验收 U1 ~ U6
 *
 * 管理端：分页列表 + 新建 / 编辑 / 删除作业。
 *
 * ⚠️ 口径（契约 §15 / 50-文档 §3）：
 * - 一律走 `api`（统一带 token / 解包 / 错误提示），页面不手搓 axios；
 * - 业务失败 = HTTP 200 + 非零 code，由 `client.ts` 统一弹错，页面不再 `ElMessage.error`；
 * - 截止时间 `due_at` 早于现在 → 后端回 `70003`（「作业已截止」），前端只透传；
 * - 删除走 `DELETE /api/homework/{id}`，成功后刷新列表。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { api } from '@/api/client'
import { formatDateTime } from '@/utils/format'

interface ClassOption {
  id: number
  name: string
}

interface UnitOption {
  id: number
  name: string
  subject: string
}

interface HomeworkItem {
  id: number
  class_id: number
  class_name: string | null
  created_by: number
  title: string
  subject: string
  unit_ids: number[]
  due_at: string | null
  status: string
  created_at: string | null
  submitted_count: number
  student_count: number
}

interface HomeworkPage {
  items: HomeworkItem[]
  total: number
  page: number
  page_size: number
}

interface ClassListPage {
  items: ClassOption[]
  total: number
  page: number
  page_size: number
}

interface HomeworkForm {
  id: number | null
  class_id: number | null
  title: string
  subject: string
  unit_ids: number[]
  due_at: Date | string | null
  status: string
}

const loading = ref(false)
const loadedOnce = ref(false)
const items = ref<HomeworkItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filterClassId = ref<number | null>(null)

const classOptions = ref<ClassOption[]>([])
const unitOptions = ref<UnitOption[]>([])

const dialogVisible = ref(false)
const submitting = ref(false)
const form = reactive<HomeworkForm>({
  id: null,
  class_id: null,
  title: '',
  subject: 'chinese',
  unit_ids: [],
  due_at: null,
  status: 'active',
})

const isEdit = ref(false)

async function loadClassOptions(): Promise<void> {
  try {
    const res = await api.get<ClassListPage>('/classes', { params: { page: 1, page_size: 200 } })
    classOptions.value = (res?.items ?? []).map((c) => ({ id: c.id, name: c.name }))
  } catch {
    // 错误由 client.ts 统一提示；下拉留空即可
  }
}

async function loadUnitOptions(): Promise<void> {
  try {
    const res = await api.get<{ items: UnitOption[] }>('/units', { params: { page: 1, page_size: 200 } })
    unitOptions.value = res?.items ?? []
  } catch {
    // 单元非必填，加载失败不影响布置
  }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const params: Record<string, unknown> = { page: page.value, page_size: pageSize.value }
    if (filterClassId.value !== null) params.class_id = filterClassId.value
    const res = await api.get<HomeworkPage>('/homework', { params })
    items.value = res?.items ?? []
    total.value = res?.total ?? 0
    loadedOnce.value = true
  } catch {
    // 错误由 client.ts 统一提示，保留上一次数据
  } finally {
    loading.value = false
  }
}

function changePage(p: number): void {
  page.value = p
  void load()
}

function openCreate(): void {
  isEdit.value = false
  form.id = null
  form.class_id = classOptions.value[0]?.id ?? null
  form.title = ''
  form.subject = 'chinese'
  form.unit_ids = []
  form.due_at = null
  form.status = 'active'
  dialogVisible.value = true
}

function openEdit(row: HomeworkItem): void {
  isEdit.value = true
  form.id = row.id
  form.class_id = row.class_id
  form.title = row.title
  form.subject = row.subject
  form.unit_ids = [...(row.unit_ids ?? [])]
  form.due_at = row.due_at ? new Date(row.due_at) : null
  form.status = row.status
  dialogVisible.value = true
}

function formatDueForSubmit(): string | null {
  const v = form.due_at
  if (v == null) return null
  if (v instanceof Date) return v.toISOString()
  return String(v)
}

async function submit(): Promise<void> {
  if (!form.title.trim()) {
    ElMessage.warning('请填写作业标题')
    return
  }
  if (form.class_id == null) {
    ElMessage.warning('请选择班级')
    return
  }
  submitting.value = true
  try {
    const due = formatDueForSubmit()
    if (isEdit.value && form.id != null) {
      await api.patch<HomeworkItem>(`/homework/${form.id}`, {
        title: form.title,
        subject: form.subject,
        unit_ids: form.unit_ids,
        due_at: due,
        status: form.status,
      })
    } else {
      await api.post<{ id: number }>('/homework', {
        class_id: form.class_id,
        title: form.title,
        subject: form.subject,
        unit_ids: form.unit_ids,
        due_at: due,
      })
    }
    dialogVisible.value = false
    await load()
  } catch {
    // client.ts 已统一提示
  } finally {
    submitting.value = false
  }
}

async function remove(row: HomeworkItem): Promise<void> {
  try {
    await ElMessageBox.confirm(`确认删除作业「${row.title}」？删除后不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.delete(`/homework/${row.id}`)
    ElMessage.success('已删除')
    await load()
  } catch {
    // client.ts 已统一提示
  }
}

function subjectLabel(s: string): string {
  return s === 'english' ? '英语' : '语文'
}

function statusLabel(s: string): string {
  return s === 'closed' ? '已关闭' : '进行中'
}

onMounted(() => {
  void loadClassOptions()
  void loadUnitOptions()
  void load()
})
</script>

<template>
  <div class="homework">
    <PageHeader title="作业布置" :show-back="false">
      <template #extra>
        <el-select
          v-model="filterClassId"
          placeholder="全部班级"
          clearable
          style="width: 160px; margin-right: 12px"
          @change="changePage(1)"
        >
          <el-option v-for="c in classOptions" :key="c.id" :label="c.name" :value="c.id" />
        </el-select>
        <el-button type="primary" @click="openCreate">布置作业</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <LoadingBox v-if="loading && !loadedOnce" />

    <EmptyState
      v-else-if="loadedOnce && items.length === 0"
      title="还没有布置作业"
      description="点击右上角「布置作业」，选择班级与截止时间，把练习任务发给学生。"
    />

    <template v-else-if="loadedOnce">
      <el-table :data="items" stripe border>
        <el-table-column prop="title" label="标题" min-width="160" />
        <el-table-column label="学科" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.subject === 'english' ? 'warning' : 'success'">
              {{ subjectLabel(row.subject) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="class_name" label="班级" min-width="110" />
        <el-table-column label="截止时间" min-width="160">
          <template #default="{ row }">
            {{ row.due_at ? formatDateTime(row.due_at) : '不限' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'closed' ? 'info' : 'success'">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="完成度" width="110">
          <template #default="{ row }">
            {{ row.submitted_count }}/{{ row.student_count }}
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="160">
          <template #default="{ row }">
            {{ row.created_at ? formatDateTime(row.created_at) : '—' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="changePage"
        />
      </div>
    </template>

    <el-dialog
      v-model="dialogVisible"
      :title="isEdit ? '编辑作业' : '布置作业'"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-width="88px">
        <el-form-item label="班级" required>
          <el-select v-model="form.class_id" placeholder="请选择班级" style="width: 100%">
            <el-option v-for="c in classOptions" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="如：背诵第 1 课生词" maxlength="100" />
        </el-form-item>
        <el-form-item label="学科">
          <el-radio-group v-model="form.subject">
            <el-radio value="chinese">语文</el-radio>
            <el-radio value="english">英语</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="关联单元">
          <el-select
            v-model="form.unit_ids"
            multiple
            filterable
            placeholder="可不选"
            style="width: 100%"
          >
            <el-option
              v-for="u in unitOptions"
              :key="u.id"
              :label="`${u.name}（${u.subject === 'english' ? '英语' : '语文'}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker
            v-model="form.due_at"
            type="datetime"
            placeholder="不选表示不限时间"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item v-if="isEdit" label="状态">
          <el-radio-group v-model="form.status">
            <el-radio value="active">进行中</el-radio>
            <el-radio value="closed">已关闭</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.homework {
  padding: 0 4px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
