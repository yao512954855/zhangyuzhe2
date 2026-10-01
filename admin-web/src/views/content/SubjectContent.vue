<script setup lang="ts">
/**
 * 学科内容管理页（英语 / 语文共用，2026-09-30 姚工定稿）—— WP-WEB-02
 *
 * 路由 `/content/english`（词条）与 `/content/chinese`（课文）共用本组件，
 * 由路由 props 固定学科 —— **筛选条没有「学科」**，一页只管一种内容。
 *
 * 布局：左树（年级 → 学期 → 单元，树头「＋新建单元」，单元节点 hover 编辑/删除）
 * + 右列表（**纯词条行 / 纯课文行**，无单元分组行）。
 *
 * - 筛选：年级 / 学期 / 单元 / 状态 / 关键词；树与筛选条双向同步
 * - 跨单元/跨年级浏览时列表带「所属单元」列（单元名 + 年级·学期后缀），
 *   锁定到具体单元后该列隐藏（页面定稿 #1）
 * - 词条列含 词性 / 例句（页面定稿 #2：宽度允许就带上，编辑弹窗里也能看）
 * - 「＋添加单词 / ＋添加课文」单条新增（页面定稿 #3，后端 POST /api/words|texts）
 * - 词条批量勾选启停、编辑弹窗（WordEditDialog）、删除保护（30003 → 转停用）全部保留
 */
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox, ElTree } from 'element-plus'
import { Delete, Edit as EditIcon, Plus } from '@element-plus/icons-vue'

import PageHeader from '@/components/PageHeader.vue'
import StatusTag from '@/components/content/StatusTag.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import WordEditDialog from './WordEditDialog.vue'
import WordCreateDialog from './WordCreateDialog.vue'
import TextCreateDialog from './TextCreateDialog.vue'
import TextContentDialog from './TextContentDialog.vue'
import {
  batchWordStatus,
  createUnit,
  createWord,
  deleteText,
  deleteUnit,
  deleteWord,
  listTextsGlobal,
  listUnitOptions,
  listWords,
  patchText,
  patchWord,
  updateUnit,
  type ContentStatus,
  type GlobalTextItem,
  type GlobalWordItem,
  type Semester,
  type Subject,
  type UnitOptionItem,
  type WordItem,
  type WordType,
} from '@/api/content'
import { BizError } from '@/api/client'

const props = defineProps<{ subject: Subject }>()

const isEnglish = computed(() => props.subject === 'english')
const pageTitle = computed(() => (isEnglish.value ? '英语内容管理' : '语文内容管理'))
const pageSubtitle = computed(() =>
  isEnglish.value ? '管理英语单元与单词、短语、句子词条' : '管理语文单元与课文',
)

const SUBJECT_LABEL: Record<Subject, string> = { english: '英语', chinese: '语文' }
const SEMESTER_LABEL: Record<Semester, string> = { first: '上学期', second: '下学期' }
const WORD_TYPE_LABEL: Record<WordType, string> = { word: '单词', phrase: '短语', sentence: '句子' }

// ----------------------------- 筛选 + 分页
const filters = reactive<{
  grade: number | ''
  semester: Semester | ''
  unit_id: number | null
  status: ContentStatus | ''
  keyword: string
}>({ grade: '', semester: '', unit_id: null, status: '', keyword: '' })

const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const words = ref<GlobalWordItem[]>([])
const texts = ref<GlobalTextItem[]>([])
const loadedOnce = ref(false)

/** 锁定到具体单元后隐藏「所属单元」列（页面定稿 #1） */
const unitLocked = computed(() => filters.unit_id !== null)

function buildQuery() {
  const q: Record<string, unknown> = { page: page.value, page_size: pageSize.value, subject: props.subject }
  if (filters.grade !== '') q.grade = filters.grade
  if (filters.semester) q.semester = filters.semester
  if (filters.unit_id !== null) q.unit_id = filters.unit_id
  if (filters.status) q.status = filters.status
  if (filters.keyword.trim()) q.keyword = filters.keyword.trim()
  return q
}

async function load() {
  loading.value = true
  try {
    if (isEnglish.value) {
      const res = await listWords(buildQuery())
      words.value = res.items
      total.value = res.total
    } else {
      const res = await listTextsGlobal(buildQuery())
      texts.value = res.items
      total.value = res.total
    }
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
  filters.grade = ''
  filters.semester = ''
  filters.unit_id = null
  filters.status = ''
  filters.keyword = ''
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

// 年级/学期变化时，已选单元若不在新范围内则清掉。
// ⚠️ 不能无条件清：树点单元时会在同一 tick 先设 grade/semester 再设 unit_id，
// watch 回调晚于这次同步赋值执行，无条件清会把刚选的单元干掉（时序坑）。
watch(
  () => [filters.grade, filters.semester],
  () => {
    if (filters.unit_id === null) return
    const u = unitOptions.value.find((o) => o.id === filters.unit_id)
    const inScope =
      !!u &&
      (filters.grade === '' || u.grade === filters.grade) &&
      (filters.semester === '' || u.semester === filters.semester)
    if (!inScope) filters.unit_id = null
  },
)

// ----------------------------- 单元下拉选项（树数据复用）
const unitOptions = ref<UnitOptionItem[]>([])
const treeLoading = ref(false)

async function refreshTree() {
  treeLoading.value = true
  try {
    const res = await listUnitOptions({ subject: props.subject })
    unitOptions.value = res.items
    await nextTick()
    syncTreeHighlight()
  } catch {
    // client 已统一弹提示
  } finally {
    treeLoading.value = false
  }
}

/** 单元下拉：随年级/学期筛选收窄（新建弹窗用同一份） */
const unitChoices = computed(() =>
  unitOptions.value.filter(
    (u) =>
      (filters.grade === '' || u.grade === filters.grade) &&
      (filters.semester === '' || u.semester === filters.semester),
  ),
)

const unitFilterOptions = computed(() =>
  unitChoices.value.map((u) => ({
    label: `${u.name}（${u.grade}年级 · ${SEMESTER_LABEL[u.semester]}）`,
    value: u.id,
  })),
)

// ----------------------------- 左树（年级 → 学期 → 单元，本学科）
type TreeKind = 'all' | 'grade' | 'semester' | 'unit'
interface TreeNode {
  key: string
  label: string
  kind: TreeKind
  children?: TreeNode[]
  grade?: number
  semester?: Semester
  unitId?: number
}

const expandedKeys = ref<string[]>(['all'])
// ⚠️ el-tree 实例方法要用 InstanceType 类型（同 §八 el-menu 的教训，别拿不到 expose）
const treeRef = ref<InstanceType<typeof ElTree> | null>(null)

function buildTree(items: UnitOptionItem[]): TreeNode[] {
  const root: TreeNode = { key: 'all', label: '全部单元', kind: 'all', children: [] }
  const grades = [...new Set(items.map((i) => i.grade))].sort((a, b) => a - b)
  for (const g of grades) {
    const gNode: TreeNode = { key: `g:${g}`, label: `${g} 年级`, kind: 'grade', grade: g, children: [] }
    for (const sem of ['first', 'second'] as Semester[]) {
      const unitList = items
        .filter((i) => i.grade === g && i.semester === sem)
        .sort((a, b) => a.order_no - b.order_no)
      if (unitList.length === 0) continue
      gNode.children!.push({
        key: `sem:${g}:${sem}`,
        label: SEMESTER_LABEL[sem],
        kind: 'semester',
        grade: g,
        semester: sem,
        children: unitList.map((u) => ({ key: `u:${u.id}`, label: u.name, kind: 'unit', unitId: u.id })),
      })
    }
    if (gNode.children!.length) root.children!.push(gNode)
  }
  return [root]
}

const treeComputed = computed<TreeNode[]>(() => buildTree(unitOptions.value))

/** 树高亮 key 由 filters 推导（树 ↔ 筛选条同步的唯一真源） */
const currentKey = computed<string>(() => {
  if (filters.unit_id !== null) return `u:${filters.unit_id}`
  if (filters.grade !== '' && filters.semester) return `sem:${filters.grade}:${filters.semester}`
  if (filters.grade !== '') return `g:${filters.grade}`
  return 'all'
})

function syncTreeHighlight() {
  const k = currentKey.value
  const chain: string[] = ['all']
  if (k.startsWith('g:')) {
    chain.push(k)
  } else if (k.startsWith('sem:')) {
    const [, g] = k.split(':')
    chain.push(`g:${g}`, k)
  } else if (k.startsWith('u:')) {
    const u = unitOptions.value.find((o) => o.id === Number(k.slice(2)))
    if (u) chain.push(`g:${u.grade}`, `sem:${u.grade}:${u.semester}`)
  }
  for (const c of chain) if (!expandedKeys.value.includes(c)) expandedKeys.value.push(c)
  treeRef.value?.setCurrentKey(k === 'all' ? 'all' : k)
}

watch(currentKey, () => syncTreeHighlight())

function onNodeClick(node: TreeNode) {
  if (node.kind === 'all') {
    filters.grade = ''
    filters.semester = ''
    filters.unit_id = null
  } else if (node.kind === 'grade') {
    filters.grade = node.grade!
    filters.semester = ''
    filters.unit_id = null
  } else if (node.kind === 'semester') {
    filters.grade = node.grade!
    filters.semester = node.semester!
    filters.unit_id = null
  } else if (node.kind === 'unit') {
    filters.grade = ''
    filters.semester = ''
    filters.unit_id = node.unitId!
  }
  page.value = 1
  load()
}

// ----------------------------- 单元 新建 / 编辑 / 删除（收进左树）
const unitDialogVisible = ref(false)
const editingUnit = ref<UnitOptionItem | null>(null)
const unitSaving = ref(false)
const unitFormRef = ref()
const unitForm = reactive({
  grade: 3 as number,
  semester: 'first' as Semester,
  name: '',
  order_no: 0,
  remark: '',
})

const unitRules = {
  grade: [
    { required: true, message: '请选择年级', trigger: 'change' },
    { type: 'number', min: 1, max: 9, message: '年级为 1–9', trigger: 'change' },
  ],
  semester: [{ required: true, message: '请选择学期', trigger: 'change' }],
  name: [{ required: true, message: '请输入单元名称', trigger: 'blur' }],
}

const gradeOptions = computed(() =>
  ([1, 2, 3, 4, 5, 6, 7, 8, 9] as const).map((g) => ({ label: `${g} 年级`, value: g })),
)
const semesterOptions = computed(() =>
  (['first', 'second'] as const).map((v) => ({ label: SEMESTER_LABEL[v], value: v })),
)

function openUnitCreate() {
  editingUnit.value = null
  unitForm.grade = filters.grade !== '' ? filters.grade : 3
  unitForm.semester = filters.semester !== '' ? filters.semester : 'first'
  unitForm.name = ''
  unitForm.order_no = 0
  unitForm.remark = ''
  unitDialogVisible.value = true
}

function openUnitEdit(node: TreeNode) {
  const u = unitOptions.value.find((o) => o.id === node.unitId)
  if (!u) return
  editingUnit.value = u
  unitForm.grade = u.grade
  unitForm.semester = u.semester
  unitForm.name = u.name
  unitForm.order_no = u.order_no
  unitForm.remark = ''
  unitDialogVisible.value = true
}

async function submitUnit() {
  await unitFormRef.value.validate()
  unitSaving.value = true
  try {
    if (editingUnit.value) {
      await updateUnit(editingUnit.value.id, {
        name: unitForm.name,
        order_no: unitForm.order_no,
        remark: unitForm.remark || null,
      })
      ElMessage.success('单元已保存')
      if (filters.unit_id === editingUnit.value.id) {
        // 名称可能变了，列表归属列与树都要刷
      }
    } else {
      await createUnit({
        subject: props.subject,
        grade: unitForm.grade,
        semester: unitForm.semester,
        name: unitForm.name,
        order_no: unitForm.order_no,
        remark: unitForm.remark || null,
      })
      ElMessage.success('单元已创建')
    }
    unitDialogVisible.value = false
    refreshTree()
    load()
  } catch {
    // 10001 等由 client 统一提示
  } finally {
    unitSaving.value = false
  }
}

async function removeUnit(node: TreeNode) {
  const u = unitOptions.value.find((o) => o.id === node.unitId)
  if (!u) return
  try {
    await ElMessageBox.confirm(
      '删除会彻底移除该单元及其下的词条/课文（不可恢复）。确定删除？',
      '确认删除',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteUnit(u.id)
    ElMessage.success('已删除')
    if (filters.unit_id === u.id) filters.unit_id = null
    refreshTree()
    load()
  } catch (e) {
    if (e instanceof BizError && e.code === 30003) {
      await unitHasRecords(u, (e.data as { record_count?: number } | null)?.record_count)
    }
  }
}

async function unitHasRecords(unit: UnitOptionItem, recordCount?: number) {
  const countText = recordCount !== undefined ? `（共 ${recordCount} 条练习记录）` : ''
  try {
    await ElMessageBox.confirm(
      `该单元已有练习记录${countText}，为保证统计完整只能停用，不能删除。\n停用后不再出题，但保留全部记录与历史成绩。`,
      '无法删除',
      { type: 'error', confirmButtonText: '改为停用', cancelButtonText: '取消', showCancelButton: true },
    )
    await updateUnit(unit.id, { status: 'inactive' })
    ElMessage.success('已改为停用')
    refreshTree()
    load()
  } catch {
    // 用户取消或失败
  }
}

// ----------------------------- 词条：批量启停 / 新建 / 编辑 / 删除
const tableRef = ref<{ clearSelection: () => void } | null>(null)
const selectedWords = ref<WordItem[]>([])
const batching = ref(false)

function onSelectionChange(rows: GlobalWordItem[]) {
  selectedWords.value = rows
}

async function batchToggle(status: ContentStatus) {
  if (selectedWords.value.length === 0) {
    ElMessage.warning('请先勾选要操作的词条')
    return
  }
  const verb = status === 'active' ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(
      `确定${verb}选中的 ${selectedWords.value.length} 个词条？${verb === '停用' ? '停用后学生端不再出题，但保留历史记录。' : ''}`,
      `批量${verb}`,
      { type: 'warning', confirmButtonText: `确认${verb}`, cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  batching.value = true
  try {
    const res = await batchWordStatus(
      selectedWords.value.map((w) => w.id),
      status,
    )
    ElMessage.success(`已${verb} ${res.updated}/${res.requested} 个词条`)
    tableRef.value?.clearSelection()
    load()
  } catch {
    // 统一提示
  } finally {
    batching.value = false
  }
}

const wordCreateVisible = ref(false)

async function handleWordCreate(payload: Parameters<typeof createWord>[0]) {
  try {
    await createWord(payload)
    ElMessage.success('词条已添加')
    wordCreateVisible.value = false
    load()
  } catch {
    // 10001 等由 client 统一提示
  }
}

const wordEditDialog = reactive<{ visible: boolean; word: WordItem | null }>({ visible: false, word: null })

function openWordEdit(row: WordItem) {
  wordEditDialog.word = row
  wordEditDialog.visible = true
}

/** 保存后据 history_affected 提示（语义同原 WordTable） */
async function handleWordSave(payload: { id: number; data: Parameters<typeof patchWord>[1] }) {
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
    wordEditDialog.visible = false
    load()
  } catch {
    // 统一提示
  }
}

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
      await wordHasRecords(row, count)
    }
  }
}

async function wordHasRecords(row: WordItem, count?: number) {
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

// ----------------------------- 课文：新建 / 启停 / 删除
const textCreateVisible = ref(false)

async function handleTextSaved() {
  load()
}

async function toggleTextStatus(row: GlobalTextItem) {
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
  try {
    await patchText(row.id, { status: next })
    ElMessage.success(`已${verb}`)
    load()
  } catch {
    // 统一提示
  }
}

async function removeText(row: GlobalTextItem) {
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
      await textHasRecords(row, count)
    }
  }
}

// ----------------------------- 课文：查看 / 行内编辑（2026-09-30）
const textDialogVisible = ref(false)
const textDialogMode = ref<'view' | 'edit'>('view')
const textDialogId = ref<number | null>(null)
const textDialogFallback = reactive<{ title: string; author: string | null }>({ title: '', author: null })

function openTextView(row: GlobalTextItem) {
  textDialogMode.value = 'view'
  textDialogId.value = row.id
  textDialogVisible.value = true
}

function openTextEdit(row: GlobalTextItem) {
  textDialogMode.value = 'edit'
  textDialogId.value = row.id
  textDialogFallback.title = row.title
  textDialogFallback.author = row.author
  textDialogVisible.value = true
}

async function textHasRecords(row: GlobalTextItem, count?: number) {
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

// ----------------------------- 状态筛选下拉
const statusFilterOptions = [
  { label: '全部', value: '' },
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
]

onMounted(async () => {
  load()
  await refreshTree()
})

// 英语/语文两个路由共用本组件，切路由时 Vue 复用组件实例、onMounted 不会再跑 ——
// 不监听 subject 的话，语文页会显示英语页的数据（跨页串台 bug，2026-09-30 姚工反馈）。
// 切换 = 整体重置：筛选、分页、树高亮、勾选全部清掉再按新学科拉数据。
watch(
  () => props.subject,
  async () => {
    filters.grade = ''
    filters.semester = ''
    filters.unit_id = null
    filters.status = ''
    filters.keyword = ''
    page.value = 1
    words.value = []
    texts.value = []
    total.value = 0
    selectedWords.value = []
    expandedKeys.value = ['all']
    treeRef.value?.setCurrentKey('all')
    load()
    await refreshTree()
  },
)
</script>

<template>
  <div class="admin-page">
    <PageHeader :title="pageTitle" :subtitle="pageSubtitle" />

    <div class="content-body">
      <!-- 左：单元目录树（含单元增删改） -->
      <el-card shadow="never" class="tree-panel">
        <template #header>
          <div class="tree-head">
            <span class="panel-title">单元目录</span>
            <el-button link type="primary" :icon="Plus" @click="openUnitCreate">新建单元</el-button>
          </div>
        </template>
        <div v-loading="treeLoading" class="tree-scroll">
          <el-tree
            ref="treeRef"
            :data="treeComputed"
            node-key="key"
            :props="{ label: 'label', children: 'children' }"
            :expand-on-click-node="false"
            :default-expanded-keys="expandedKeys"
            highlight-current
            :empty-text="`暂无${SUBJECT_LABEL[subject]}单元`"
            @node-click="onNodeClick"
          >
            <template #default="{ data }">
              <span class="tree-node">
                <span class="tree-node__label" :class="`tree-node--${data.kind}`">{{ data.label }}</span>
                <span v-if="data.kind === 'unit'" class="tree-node__ops">
                  <el-icon class="tree-node__op" @click.stop="openUnitEdit(data)"><EditIcon /></el-icon>
                  <el-icon class="tree-node__op tree-node__op--danger" @click.stop="removeUnit(data)"><Delete /></el-icon>
                </span>
              </span>
            </template>
          </el-tree>
        </div>
      </el-card>

      <!-- 右：筛选 + 内容列表（纯词条行 / 纯课文行） -->
      <div class="list-panel">
        <el-card shadow="never" class="filter-card">
          <div class="filter-bar">
            <el-form :inline="true" @submit.prevent>
              <el-form-item label="年级">
                <el-select v-model="filters.grade" placeholder="全部" clearable style="width: 110px" @change="onSearch">
                  <el-option v-for="o in gradeOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="学期">
                <el-select v-model="filters.semester" placeholder="全部" clearable style="width: 120px" @change="onSearch">
                  <el-option v-for="o in semesterOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="单元">
                <el-select v-model="filters.unit_id" placeholder="全部单元" clearable filterable style="width: 200px" @change="onSearch">
                  <el-option v-for="o in unitFilterOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>
              <el-form-item label="状态">
                <el-select v-model="filters.status" placeholder="全部" style="width: 100px" @change="onSearch">
                  <el-option v-for="o in statusFilterOptions" :key="o.value" :label="o.label" :value="o.value" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-input
                  v-model="filters.keyword"
                  :placeholder="isEnglish ? '搜英文/中文' : '搜标题/作者'"
                  clearable
                  style="width: 170px"
                  @keyup.enter="onSearch"
                  @clear="onSearch"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="onSearch">查询</el-button>
                <el-button @click="onReset">重置</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-card>

        <!-- 工具条：批量启停（词条）+ 单条新增 -->
        <div class="toolbar">
          <template v-if="isEnglish">
            <el-button size="small" type="success" :disabled="selectedWords.length === 0" :loading="batching" @click="batchToggle('active')">
              批量启用
            </el-button>
            <el-button size="small" type="warning" :disabled="selectedWords.length === 0" :loading="batching" @click="batchToggle('inactive')">
              批量停用
            </el-button>
            <span class="toolbar__hint">已选 {{ selectedWords.length }} 个词条</span>
          </template>
          <span v-else class="toolbar__hint">点「查看」预览课文分段与背诵范围；改正文会重算拼音校验</span>
          <span class="toolbar__spacer" />
          <el-button v-if="isEnglish" type="primary" size="small" @click="wordCreateVisible = true">＋添加单词</el-button>
          <el-button v-else type="primary" size="small" @click="textCreateVisible = true">＋添加课文</el-button>
        </div>

        <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8" class="table-wrap">
          <EmptyState
            v-if="loadedOnce && (isEnglish ? words.length === 0 : texts.length === 0)"
            :description="isEnglish ? '没有符合条件的词条' : '没有符合条件的课文'"
          />

          <!-- ============ 英语：词条列表 ============ -->
          <template v-else-if="isEnglish">
            <el-table
              ref="tableRef"
              :data="words"
              :row-key="(r: GlobalWordItem) => `w-${r.id}`"
              border
              stripe
              @selection-change="onSelectionChange"
            >
              <el-table-column type="selection" width="42" reserve-selection />
              <el-table-column label="类型" width="70">
                <template #default="{ row }">{{ WORD_TYPE_LABEL[row.type as WordType] || row.type }}</template>
              </el-table-column>
              <el-table-column prop="en" label="英文" min-width="130" show-overflow-tooltip />
              <el-table-column prop="cn" label="中文" min-width="110" show-overflow-tooltip />
              <el-table-column label="词性" width="70">
                <template #default="{ row }">{{ row.pos || '—' }}</template>
              </el-table-column>
              <el-table-column label="可接受答案" min-width="130" show-overflow-tooltip>
                <template #default="{ row }">{{ (row.accepted || []).join('、') || '—' }}</template>
              </el-table-column>
              <el-table-column prop="phonetic" label="音标" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">{{ row.phonetic || '—' }}</template>
              </el-table-column>
              <el-table-column label="例句" min-width="160" show-overflow-tooltip>
                <template #default="{ row }">{{ row.example_en || '—' }}</template>
              </el-table-column>
              <el-table-column label="拼读" width="60" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.require_spell ? 'primary' : 'info'" size="small">
                    {{ row.require_spell ? '是' : '否' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column v-if="!unitLocked" label="所属单元" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">
                  {{ row.unit_name }}（{{ row.grade }}年级 · {{ SEMESTER_LABEL[row.semester as Semester] }}）
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80" align="center">
                <template #default="{ row }"><StatusTag :status="row.status" /></template>
              </el-table-column>
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openWordEdit(row)">编辑</el-button>
                  <el-button link type="danger" @click="removeWord(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>

          <!-- ============ 语文：课文列表 ============ -->
          <template v-else>
            <el-table :data="texts" :row-key="(r: GlobalTextItem) => `t-${r.id}`" border stripe>
              <el-table-column prop="title" label="标题" min-width="150" show-overflow-tooltip />
              <el-table-column label="作者" min-width="110" show-overflow-tooltip>
                <template #default="{ row }">{{ row.author || '—' }}</template>
              </el-table-column>
              <el-table-column v-if="!unitLocked" label="所属单元" min-width="170" show-overflow-tooltip>
                <template #default="{ row }">
                  {{ row.unit_name }}（{{ row.grade }}年级 · {{ SEMESTER_LABEL[row.semester as Semester] }}）
                </template>
              </el-table-column>
              <el-table-column label="总字数" width="90" align="center">
                <template #default="{ row }">{{ row.total_chars }}</template>
              </el-table-column>
              <el-table-column label="背诵字数" width="90" align="center">
                <template #default="{ row }">{{ row.recite_scope_chars }}</template>
              </el-table-column>
              <el-table-column label="段落数" width="80" align="center">
                <template #default="{ row }">{{ row.paragraph_count }}</template>
              </el-table-column>
              <el-table-column label="状态" width="80" align="center">
                <template #default="{ row }"><StatusTag :status="row.status" /></template>
              </el-table-column>
              <el-table-column label="操作" width="240" fixed="right" class-name="op-cell">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openTextView(row)">查看</el-button>
                  <el-button link type="primary" @click="openTextEdit(row)">编辑</el-button>
                  <el-button link type="warning" @click="toggleTextStatus(row)">
                    {{ row.status === 'active' ? '停用' : '启用' }}
                  </el-button>
                  <el-button link type="danger" @click="removeText(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>

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
      </div>
    </div>

    <!-- 单元 新建 / 编辑（subject 固定为本页学科） -->
    <el-dialog v-model="unitDialogVisible" :title="editingUnit ? '编辑单元' : '新建单元'" width="440px">
      <el-form ref="unitFormRef" :model="unitForm" :rules="unitRules" label-width="80px">
        <el-form-item label="学科">
          <el-input :model-value="SUBJECT_LABEL[subject]" disabled />
        </el-form-item>
        <el-form-item label="年级" prop="grade">
          <el-select v-model="unitForm.grade" :disabled="!!editingUnit" style="width: 100%">
            <el-option v-for="g in gradeOptions" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="学期" prop="semester">
          <el-select v-model="unitForm.semester" :disabled="!!editingUnit" style="width: 100%">
            <el-option v-for="o in semesterOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="unitForm.name" :placeholder="isEnglish ? '如 Unit 1 Hello!' : '如 第一单元'" />
        </el-form-item>
        <el-form-item label="序号">
          <el-input-number v-model="unitForm.order_no" :min="0" :max="9999" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="unitForm.remark" type="textarea" :rows="2" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unitDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="unitSaving" @click="submitUnit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 单条新增词条 -->
    <WordCreateDialog
      v-model:visible="wordCreateVisible"
      :units="unitChoices"
      :default-unit-id="filters.unit_id"
      @save="handleWordCreate"
    />

    <!-- 单条新增课文 -->
    <TextCreateDialog
      v-model:visible="textCreateVisible"
      :units="unitChoices"
      :default-unit-id="filters.unit_id"
      @saved="handleTextSaved"
    />

    <!-- 课文 查看 / 编辑（2026-09-30） -->
    <TextContentDialog
      v-model:visible="textDialogVisible"
      :mode="textDialogMode"
      :text-id="textDialogId"
      :fallback-title="textDialogFallback.title"
      :fallback-author="textDialogFallback.author"
      @saved="handleTextSaved"
    />

    <!-- 词条编辑（复用） -->
    <WordEditDialog
      v-model:visible="wordEditDialog.visible"
      :word="wordEditDialog.word"
      @save="handleWordSave"
    />
  </div>
</template>

<style scoped>
/* ---- 左树右表 ---- */
.content-body {
  display: flex;
  align-items: flex-start;
  gap: 16px;
}
.tree-panel {
  flex: 0 0 240px;
  width: 240px;
}
.tree-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.panel-title {
  font-weight: 600;
}
.tree-scroll {
  min-height: 200px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}
.tree-node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  font-size: 13px;
}
.tree-node__label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tree-node--unit {
  color: var(--el-color-primary, #409eff);
}
/* 单元节点 hover 才出现 编辑/删除 小图标 */
.tree-node__ops {
  display: none;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}
.tree-node:hover .tree-node__ops {
  display: inline-flex;
}
.tree-node__op {
  color: var(--el-color-primary, #409eff);
  cursor: pointer;
}
.tree-node__op--danger {
  color: var(--el-color-danger, #f56c6c);
}
.list-panel {
  flex: 1 1 auto;
  min-width: 0;
}
.filter-card {
  margin-bottom: 12px;
}
.filter-bar {
  display: flex;
  align-items: flex-start;
}
.table-wrap {
  background: var(--admin-panel);
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.toolbar__hint {
  color: var(--admin-text-secondary, #909399);
  font-size: 13px;
}
.toolbar__spacer {
  flex: 1 1 auto;
}
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0;
}

/* 操作列按钮不换行（查看/编辑/停用/删除 一行排开） */
:deep(.op-cell .cell) {
  white-space: nowrap;
}
</style>
