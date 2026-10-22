<script setup lang="ts">
/**
 * 内容录入页 —— WP-B 功能点 4-9。
 *
 * - 左侧 UnitTree 选单元，右侧 word/phrase/sentence 三 tab（枚举英文值，中文映射仅 UI，契约 §7）；
 * - 表格四列：英文 | 词性（选填）| 中文 | 其他可接受答案（选填，顿号分隔）；行内编辑、顶部新增、删除确认；
 * - en 同单元同类型重复：前端先提示，后端 20003 兜底（message 由 client.ts 统一弹出）；
 * - Excel 导入（结果弹窗 inserted/duplicated/failed，失败行含 row+reason）/ 导出 / 模板下载；
 * - keyword 搜索走 `GET /api/items?keyword=`，前端不做本地过滤。
 */
import { computed, onActivated, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import UnitTree from '@/components/UnitTree.vue'
import {
  ITEM_TYPES,
  ITEM_TYPE_LABEL,
  batchDeleteItems,
  batchMoveItems,
  createItem,
  deleteItem,
  downloadImportTemplate,
  exportItems,
  importItems,
  listItems,
  updateItem,
} from '@/api/items'
import { isLeafUnit } from '@/api/types'
import type { ItemImportResult, ItemRow, ItemType, UnitNode } from '@/api/types'

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

/** 当前选中单元（null = 未选）。 */
const selectedUnit = ref<UnitNode | null>(null)
const treeRef = ref<InstanceType<typeof UnitTree> | null>(null)

/** 当前 tab（英文枚举值，契约 §7）。 */
const activeType = ref<ItemType>('word')

const loading = ref(false)
const loadedOnce = ref(false)
const busy = ref(false)

const rows = ref<ItemRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const keywordInput = ref('')
/** 已生效的搜索词（点「搜索」才应用，走服务端过滤）。 */
const appliedKeyword = ref('')

/** 页头副标题。 */
const subtitle = computed(() => {
  if (!selectedUnit.value) return '先在左侧选择单元'
  const leafHint = isLeafUnit(selectedUnit.value) ? '' : '（有下级分类，不能录入内容）'
  return `${selectedUnit.value.name} · ${ITEM_TYPE_LABEL[activeType.value]}${leafHint}`
})

/** 当前选中是否末级分类（todo13 #1：非叶节点不能录入/导入内容）。 */
const selectedIsLeaf = computed(() => isLeafUnit(selectedUnit.value))

// ---------------------------------------------------------------------------
// 行内编辑 / 新增
// ---------------------------------------------------------------------------

interface DraftRow {
  en: string
  pos: string
  zh: string
  alt_zh: string
}

/** 表格行 = 服务端行 或 新增草稿行（id=CREATE_ROW_ID 占位）。 */
type EditableRow = ItemRow | (DraftRow & { id: number })

/** 新增草稿行占位 id（与真实自增 id 区分；id 从 1 起不会撞）。 */
const CREATE_ROW_ID = 0

/** 正在编辑的已有行 id；null = 无。 */
const editingId = ref<number | null>(null)
const editDraft = reactive<DraftRow>({ en: '', pos: '', zh: '', alt_zh: '' })

/** 新增行开关 + 草稿（渲染在列表最上面）。 */
const creating = ref(false)
const createDraft = reactive<DraftRow>({ en: '', pos: '', zh: '', alt_zh: '' })

/** 表格数据：新增草稿行 + 服务端当前页。 */
const tableData = computed<EditableRow[]>(() => {
  const draft: EditableRow[] = creating.value ? [{ id: CREATE_ROW_ID, ...createDraft }] : []
  return [...draft, ...rows.value]
})

function rowClassName({ row }: { row: EditableRow }): string {
  return row.id === CREATE_ROW_ID ? 'item-table__new-row' : ''
}

function startCreate(): void {
  if (!selectedUnit.value) {
    ElMessage.warning('请先在左侧选择单元')
    return
  }
  if (!selectedIsLeaf.value) {
    ElMessage.warning('该分类下有下级分类，请选择末级分类后再录入内容')
    return
  }
  cancelEdit()
  createDraft.en = ''
  createDraft.pos = ''
  createDraft.zh = ''
  createDraft.alt_zh = ''
  creating.value = true
}

function startEdit(row: ItemRow): void {
  creating.value = false
  editingId.value = row.id
  editDraft.en = row.en
  editDraft.pos = row.pos
  editDraft.zh = row.zh
  editDraft.alt_zh = row.alt_zh
}

function cancelEdit(): void {
  editingId.value = null
  creating.value = false
}

/** 前端判重（U5 第一道）：同单元同类型下 en 重复（trim 后精确比，与后端口径一致）。 */
function findDuplicateEn(en: string, excludeId: number): ItemRow | undefined {
  const target = en.trim()
  return rows.value.find((r) => r.id !== excludeId && r.en.trim() === target)
}

/** 保存（新增草稿行 或 行内编辑）。 */
async function saveRow(row: EditableRow): Promise<void> {
  if (!selectedUnit.value) return
  const isDraft = row.id === CREATE_ROW_ID
  const draft = isDraft ? createDraft : editDraft
  const en = draft.en.trim()
  const zh = draft.zh.trim()
  if (!en) {
    ElMessage.warning('英文不能为空')
    return
  }
  if (!zh) {
    ElMessage.warning('中文释义不能为空')
    return
  }
  if (findDuplicateEn(en, isDraft ? -1 : row.id)) {
    ElMessage.warning(`「${en}」在当前单元的${ITEM_TYPE_LABEL[activeType.value]}下已存在`)
    return
  }

  busy.value = true
  try {
    const payload = { en, pos: draft.pos.trim(), zh, alt_zh: draft.alt_zh.trim() }
    if (isDraft) {
      await createItem({ unit_id: selectedUnit.value.id, type: activeType.value, ...payload })
      ElMessage.success('已新增')
      creating.value = false
    } else {
      await updateItem(row.id, payload)
      ElMessage.success('已保存')
      editingId.value = null
    }
    await load()
  } catch {
    // 后端 20003 等业务错误由 client.ts 统一弹 message（U5 兜底）
  } finally {
    busy.value = false
  }
}

async function confirmDelete(row: ItemRow): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除「${row.en}」？该操作不可恢复。`, '删除内容', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  busy.value = true
  try {
    await deleteItem(row.id)
    ElMessage.success('已删除')
    // 当前页删空后回退一页
    if (rows.value.length === 1 && page.value > 1) page.value -= 1
    await load()
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

// ---------------------------------------------------------------------------
// 加载 / 搜索 / 分页 / tab
// ---------------------------------------------------------------------------

async function load(): Promise<void> {
  if (!selectedUnit.value) {
    rows.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const result = await listItems({
      unit_id: selectedUnit.value.id,
      type: activeType.value,
      page: page.value,
      page_size: pageSize.value,
      keyword: appliedKeyword.value || undefined,
    })
    rows.value = result.items
    total.value = result.total
    loadedOnce.value = true
    // 搜索/删除后页码可能越界，收敛
    const maxPage = Math.max(1, Math.ceil(result.total / pageSize.value))
    if (page.value > maxPage) {
      page.value = maxPage
    }
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function handleSizeChange(): void {
  page.value = 1
  load()
}

function doSearch(): void {
  appliedKeyword.value = keywordInput.value.trim()
  page.value = 1
  load()
}

function clearSearch(): void {
  keywordInput.value = ''
  doSearch()
}

watch(activeType, () => {
  cancelEdit()
  appliedKeyword.value = keywordInput.value.trim()
  page.value = 1
  load()
})

async function handleSelectUnit(unit: UnitNode): Promise<void> {
  selectedUnit.value = unit
  cancelEdit()
  page.value = 1
  await load()
}

// ---------------------------------------------------------------------------
// Excel 导入 / 导出 / 模板
// ---------------------------------------------------------------------------

const fileInputRef = ref<HTMLInputElement | null>(null)
const importResultVisible = ref(false)
const importResult = ref<ItemImportResult | null>(null)

function pickImportFile(): void {
  if (!selectedUnit.value) {
    ElMessage.warning('请先在左侧选择单元')
    return
  }
  if (!selectedIsLeaf.value) {
    ElMessage.warning('该分类下有下级分类，请选择末级分类后再导入内容')
    return
  }
  fileInputRef.value?.click()
}

async function handleImportFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许连续选同一个文件
  if (!file || !selectedUnit.value) return

  busy.value = true
  try {
    importResult.value = await importItems(file, selectedUnit.value.id, activeType.value)
    importResultVisible.value = true
    await load()
    treeRef.value?.refresh() // 单元 counts 变了，同步左侧树角标
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

async function doExport(): Promise<void> {
  if (!selectedUnit.value) return
  busy.value = true
  try {
    // todo13 #3：导出选中分类的**全部类型**（混导带「类型」列），不再按 tab 细分
    await exportItems(selectedUnit.value.id)
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

async function doDownloadTemplate(): Promise<void> {
  busy.value = true
  try {
    await downloadImportTemplate()
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

// keep-alive 下首次进入也会触发 onActivated：刷新左侧树角标（counts），
// 并在已选单元时重拉列表（分类页可能级联删过内容）
onActivated(() => {
  treeRef.value?.refresh()
  if (selectedUnit.value) load()
})

// ---------------------------------------------------------------------------
// 批量删除 / 批量移动（todo13 #2）
// ---------------------------------------------------------------------------

/** 勾选的条目 id（仅真实行，新增草稿行 selectable=false 不会进）。 */
const selectedIds = ref<number[]>([])

function handleSelectionChange(rows: ItemRow[]): void {
  selectedIds.value = rows.map((r) => r.id)
}

/** 清空勾选（批量栏「取消勾选」）。 */
function clearSelection(): void {
  selectedIds.value = []
}

async function doBatchDelete(): Promise<void> {
  const ids = selectedIds.value
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(`删除选中的 ${ids.length} 条内容？该操作不可恢复。`, '批量删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  busy.value = true
  try {
    const result = await batchDeleteItems(ids)
    ElMessage.success(
      result.missing.length > 0
        ? `已删除 ${result.deleted} 条，${result.missing.length} 条不存在`
        : `已删除 ${result.deleted} 条`,
    )
    selectedIds.value = []
    await load()
    treeRef.value?.refresh()
  } catch {
    // 统一提示
  } finally {
    busy.value = false
  }
}

// 批量移动弹窗的 v-model 载体（handleMoveTargetSelect 同步拿到对象）
const moveTargetId = ref<number | null>(null)

// 批量移动：弹窗里用 UnitTree 选目标分类（必须是末级分类）
const moveDialogVisible = ref(false)
const moveTarget = ref<UnitNode | null>(null)

function openBatchMove(): void {
  if (selectedIds.value.length === 0) return
  moveTarget.value = null
  moveTargetId.value = null
  moveDialogVisible.value = true
}

function handleMoveTargetSelect(unit: UnitNode): void {
  moveTarget.value = unit
}

async function submitBatchMove(): Promise<void> {
  if (!moveTarget.value) {
    ElMessage.warning('请先在左侧选择目标分类')
    return
  }
  if (!isLeafUnit(moveTarget.value)) {
    ElMessage.warning('目标分类下有下级分类，请选择末级分类')
    return
  }
  if (selectedUnit.value && moveTarget.value.id === selectedUnit.value.id) {
    ElMessage.warning('目标分类就是当前分类，无需移动')
    return
  }

  busy.value = true
  try {
    const result = await batchMoveItems(selectedIds.value, moveTarget.value.id)
    const hint =
      result.skipped.length > 0
        ? `已移动 ${result.moved} 条，${result.skipped.length} 条因目标分类已有同名内容跳过`
        : `已移动 ${result.moved} 条`
    ElMessage.success(hint)
    moveDialogVisible.value = false
    selectedIds.value = []
    await load()
    treeRef.value?.refresh() // 两个分类的 counts 都变了
  } catch {
    // 统一提示（含目标非叶 10001 兜底）
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="admin-page">
    <PageHeader title="内容录入" :subtitle="subtitle">
      <el-button :disabled="!selectedUnit" @click="doDownloadTemplate">模板下载</el-button>
      <el-button :disabled="!selectedUnit" @click="doExport">导出 Excel（全部类型）</el-button>
      <el-button
        type="primary"
        :disabled="!selectedUnit || !selectedIsLeaf"
        @click="pickImportFile"
      >Excel 导入</el-button>
      <el-button type="primary" :disabled="!selectedIsLeaf" @click="startCreate">新增一行</el-button>
    </PageHeader>

    <input
      ref="fileInputRef"
      type="file"
      accept=".xlsx"
      class="item-table__file-input"
      @change="handleImportFile"
    />

    <el-row :gutter="16">
      <!-- 左：单元树 -->
      <el-col :span="6">
        <el-card shadow="never" class="item-table__side-card">
          <template #header>
            <div class="item-table__card-header">
              <span>单元</span>
              <span v-if="selectedUnit" class="item-table__card-hint">{{ selectedUnit.name }}</span>
            </div>
          </template>
          <UnitTree ref="treeRef" @select="handleSelectUnit" />
        </el-card>
      </el-col>

      <!-- 右：三 tab 内容表 -->
      <el-col :span="18">
        <el-card shadow="never" class="item-table__main-card">
          <template #header>
            <div class="item-table__card-header item-table__toolbar">
              <el-tabs v-model="activeType" class="item-table__tabs">
                <el-tab-pane v-for="t in ITEM_TYPES" :key="t" :label="ITEM_TYPE_LABEL[t]" :name="t" />
              </el-tabs>

              <div class="item-table__search">
                <el-input
                  v-model="keywordInput"
                  placeholder="搜索英文或中文"
                  clearable
                  @keyup.enter="doSearch"
                  @clear="clearSearch"
                >
                  <template #append>
                    <el-button @click="doSearch">搜索</el-button>
                  </template>
                </el-input>
              </div>
            </div>
          </template>

          <template v-if="!selectedUnit">
            <EmptyState description="请先在左侧选择一个单元" />
          </template>

          <template v-else>
            <!-- 批量操作栏（todo13 #2）：有勾选才出现 -->
            <div v-if="selectedIds.length > 0" class="item-table__batch-bar">
              <span>已选 {{ selectedIds.length }} 条</span>
              <el-button type="danger" size="small" :disabled="busy" @click="doBatchDelete">
                批量删除
              </el-button>
              <el-button type="primary" size="small" :disabled="busy" @click="openBatchMove">
                批量移动到…
              </el-button>
              <el-button size="small" @click="clearSelection">取消勾选</el-button>
            </div>

            <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8">
              <el-table
                :data="tableData"
                border
                stripe
                size="small"
                v-loading="busy"
                :row-class-name="rowClassName"
                @selection-change="handleSelectionChange"
              >
                <el-table-column
                  type="selection"
                  width="42"
                  :selectable="(row: EditableRow) => row.id !== 0"
                />

                <el-table-column label="英文" min-width="180">
                  <template #default="{ row }">
                    <el-input v-if="row.id === 0" v-model="createDraft.en" size="small" placeholder="必填" />
                    <el-input v-else-if="editingId === row.id" v-model="editDraft.en" size="small" placeholder="必填" />
                    <span v-else class="item-table__en">{{ row.en }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="词性" width="120">
                  <template #default="{ row }">
                    <el-input v-if="row.id === 0" v-model="createDraft.pos" size="small" placeholder="选填，如 adj." />
                    <el-input v-else-if="editingId === row.id" v-model="editDraft.pos" size="small" placeholder="选填，如 adj." />
                    <span v-else>{{ row.pos || '—' }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="中文" min-width="180">
                  <template #default="{ row }">
                    <el-input v-if="row.id === 0" v-model="createDraft.zh" size="small" placeholder="必填" />
                    <el-input v-else-if="editingId === row.id" v-model="editDraft.zh" size="small" placeholder="必填" />
                    <span v-else>{{ row.zh }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="其他可接受答案" min-width="200">
                  <template #default="{ row }">
                    <el-input v-if="row.id === 0" v-model="createDraft.alt_zh" size="small" placeholder="选填，顿号分隔" />
                    <el-input v-else-if="editingId === row.id" v-model="editDraft.alt_zh" size="small" placeholder="选填，顿号分隔" />
                    <span v-else>{{ row.alt_zh || '—' }}</span>
                  </template>
                </el-table-column>

                <el-table-column label="操作" width="150" align="center">
                  <template #default="{ row }">
                    <template v-if="row.id === 0 || editingId === row.id">
                      <el-button link type="primary" size="small" :disabled="busy" @click="saveRow(row)">保存</el-button>
                      <el-button link size="small" @click="cancelEdit">取消</el-button>
                    </template>
                    <template v-else>
                      <el-button link type="primary" size="small" @click="startEdit(row)">编辑</el-button>
                      <el-button link type="danger" size="small" @click="confirmDelete(row)">删除</el-button>
                    </template>
                  </template>
                </el-table-column>

                <template #empty>
                  <EmptyState description="暂无内容，点击「新增一行」或用 Excel 批量导入">
                    <el-button type="primary" @click="startCreate">新增一行</el-button>
                  </EmptyState>
                </template>
              </el-table>

              <div class="item-table__pager">
                <el-pagination
                  v-model:current-page="page"
                  v-model:page-size="pageSize"
                  :total="total"
                  :page-sizes="[20, 50, 100]"
                  layout="total, sizes, prev, pager, next"
                  @current-change="load"
                  @size-change="handleSizeChange"
                />
              </div>
            </LoadingBox>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <!-- 批量移动弹窗（todo13 #2）：左侧选目标分类，必须是末级 -->
    <el-dialog v-model="moveDialogVisible" title="批量移动" width="480px" :close-on-click-modal="false">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="在下方选择目标分类（必须是末级分类）；目标已有同名内容的行为自动跳过"
        class="item-table__move-alert"
      />
      <UnitTree v-model="moveTargetId" @select="handleMoveTargetSelect" />
      <template #footer>
        <el-button @click="moveDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitBatchMove">移动</el-button>
      </template>
    </el-dialog>

    <!-- 导入结果弹窗（U8） -->
    <el-dialog v-model="importResultVisible" title="导入结果" width="520px">
      <template v-if="importResult">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="新增">{{ importResult.inserted }}</el-descriptions-item>
          <el-descriptions-item label="重复跳过">{{ importResult.duplicated }}</el-descriptions-item>
          <el-descriptions-item label="失败">{{ importResult.failed.length }}</el-descriptions-item>
        </el-descriptions>

        <template v-if="importResult.failed.length > 0">
          <div class="item-table__failed-title">失败明细（Excel 行号 + 原因）</div>
          <el-table :data="importResult.failed" border size="small" max-height="260">
            <el-table-column prop="row" label="行号" width="80" align="center" />
            <el-table-column prop="reason" label="原因" min-width="240" />
          </el-table>
        </template>
      </template>
      <template #footer>
        <el-button type="primary" @click="importResultVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.item-table__side-card,
.item-table__main-card {
  margin-bottom: 16px;
}

.item-table__card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.item-table__card-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}

.item-table__toolbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.item-table__tabs {
  flex: 1 0 auto;
}

.item-table__tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}

.item-table__search {
  width: 260px;
}

.item-table__file-input {
  display: none;
}

.item-table__batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  margin-bottom: 10px;
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
  font-size: 13px;
}

.item-table__move-alert {
  margin-bottom: 10px;
}

.item-table__en {
  font-weight: 600;
}

.item-table__pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

:deep(.item-table__new-row) {
  background: var(--el-color-primary-light-9);
}
</style>
