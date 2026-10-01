<script setup lang="ts">
/**
 * 内容变更记录 —— WP-2I + 2026-09-30 方案 B 重构（姚工拍板）。
 *
 * 两级结构，不再要求手填实体 ID：
 *   1. 默认：全局最近变更列表（`GET /content/versions/recent`），可按类型/动作过滤；
 *   2. 点「变更历史」：进入该实体的版本历史（`with_snapshot=1`），
 *      前端按相邻快照算字段级 diff，展开行看「改了什么」，并可回滚。
 *
 * 回滚**不会删除**任何内容（后端保证），有记录的内容也安全。
 */
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import {
  listRecentVersions,
  listVersions,
  rollbackVersion,
  type RecentVersionItem,
  type VersionEntityType,
  type VersionItem,
  type VersionOp,
} from '@/api/versions'

// ---------------------------------------------------------------------------
// 字典
// ---------------------------------------------------------------------------

const TYPE_LABEL: Record<VersionEntityType, string> = { unit: '单元', word: '词条', text: '课文' }
const OP_LABEL: Record<VersionOp, string> = {
  create: '新增',
  update: '修改',
  disable: '停用',
  delete: '删除',
}
const OP_TAG: Record<VersionOp, 'success' | 'primary' | 'warning' | 'danger'> = {
  create: 'success',
  update: 'primary',
  disable: 'warning',
  delete: 'danger',
}

/** 参与字段级 diff 的字段（按实体类型），key 顺序即展示顺序 */
const FIELD_LABELS: Record<VersionEntityType, Record<string, string>> = {
  unit: {
    name: '名称',
    subject: '学科',
    grade: '年级',
    semester: '学期',
    order_no: '排序',
    remark: '备注',
    status: '状态',
  },
  word: {
    type: '类型',
    en: '英文',
    cn: '中文',
    accepted: '可接受答案',
    phonetic: '音标',
    pos: '词性',
    example_en: '英文例句',
    example_cn: '中文例句',
    require_spell: '需要拼读',
    order_no: '排序',
    status: '状态',
    unit_id: '所属单元',
  },
  text: {
    title: '标题',
    author: '作者',
    content_raw: '正文',
    recite_scope: '背诵范围',
    total_chars: '总字数',
    order_no: '排序',
    status: '状态',
    unit_id: '所属单元',
  },
}

function fmtVal(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '（空）'
  switch (key) {
    case 'status':
      return v === 'active' ? '启用' : '停用'
    case 'subject':
      return v === 'english' ? '英语' : '语文'
    case 'semester':
      return v === 'first' ? '上学期' : '下学期'
    case 'grade':
      return `${v} 年级`
    case 'require_spell':
      return v === 1 || v === true ? '是' : '否'
    case 'type':
      return v === 'word' ? '单词' : v === 'phrase' ? '词组' : '句子'
    case 'unit_id':
      return `单元 #${v}`
    case 'recite_scope':
      if (Array.isArray(v)) {
        return v.length ? `第 ${v.map((i) => i + 1).join('、')} 段` : '全文'
      }
      return String(v)
    default: {
      const s = String(v)
      return s.length > 50 ? s.slice(0, 50) + '…' : s
    }
  }
}

interface DiffEntry {
  field: string
  from: string
  to: string
}

function buildDiff(
  entityType: VersionEntityType,
  prev: Record<string, unknown> | undefined,
  curr: Record<string, unknown> | undefined,
): DiffEntry[] {
  const out: DiffEntry[] = []
  for (const [key, label] of Object.entries(FIELD_LABELS[entityType])) {
    const a = prev?.[key]
    const b = curr?.[key]
    if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) {
      out.push({ field: label, from: fmtVal(key, a), to: fmtVal(key, b) })
    }
  }
  return out
}

function fmtTime(s: string | null): string {
  return s ? s.replace('T', ' ').slice(0, 19) : '—'
}

/** 最近变更列表的归属上下文：`英语 · 3 年级 · 上学期 · Unit 1` */
function contextText(row: RecentVersionItem): string {
  const parts: string[] = []
  if (row.subject) parts.push(row.subject === 'english' ? '英语' : '语文')
  if (row.grade) parts.push(`${row.grade} 年级`)
  if (row.semester) parts.push(row.semester === 'first' ? '上学期' : '下学期')
  if (row.unit_name) parts.push(row.unit_name)
  return parts.join(' · ')
}

// ---------------------------------------------------------------------------
// 视图 1：全局最近变更
// ---------------------------------------------------------------------------

const filterType = ref<VersionEntityType | ''>('')
const filterOp = ref<VersionOp | ''>('')
const recentLoading = ref(false)
const recentLoaded = ref(false)
const recentItems = ref<RecentVersionItem[]>([])
const recentTotal = ref(0)
const recentPage = ref(1)
const PAGE_SIZE = 20

async function loadRecent() {
  recentLoading.value = true
  try {
    const res = await listRecentVersions({
      entity_type: filterType.value,
      op: filterOp.value,
      page: recentPage.value,
      page_size: PAGE_SIZE,
    })
    recentItems.value = res.items
    recentTotal.value = res.total
    recentLoaded.value = true
  } catch {
    // client 已统一弹提示
  } finally {
    recentLoading.value = false
  }
}

function onFilterChange() {
  recentPage.value = 1
  void loadRecent()
}

function onRecentPageChange(p: number) {
  recentPage.value = p
  void loadRecent()
}

// ---------------------------------------------------------------------------
// 视图 2：单实体版本历史（diff + 回滚）
// ---------------------------------------------------------------------------

const mode = ref<'recent' | 'history'>('recent')
const historyLoading = ref(false)
const versions = ref<VersionItem[]>([])
const current = reactive<{ type: VersionEntityType; id: number; name: string }>({
  type: 'unit',
  id: 0,
  name: '',
})
/** version 记录 id → 相邻快照 diff（历史视图加载时算好） */
const diffMap = ref<Map<number, DiffEntry[]>>(new Map())

const historySubtitle = computed(
  () => `${TYPE_LABEL[current.type]}「${current.name || `#${current.id}`}」的全部版本，可展开查看具体变更内容`,
)

async function openHistory(row: RecentVersionItem) {
  current.type = row.entity_type
  current.id = row.entity_id
  current.name = row.entity_name || `#${row.entity_id}`
  mode.value = 'history'
  await loadHistory()
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await listVersions(current.type, current.id, true)
    const asc = res.items
    const map = new Map<number, DiffEntry[]>()
    for (let i = 0; i < asc.length; i++) {
      const item = asc[i]
      if (item.op === 'create' || item.op === 'delete') {
        map.set(item.id, [])
        continue
      }
      const prev = i > 0 ? asc[i - 1].snapshot : undefined
      map.set(item.id, buildDiff(current.type, prev, item.snapshot))
    }
    diffMap.value = map
    versions.value = [...asc].reverse() // 最新在前
  } catch {
    // client 已统一弹提示
  } finally {
    historyLoading.value = false
  }
}

function backToRecent() {
  mode.value = 'recent'
  void loadRecent()
}

function diffSummary(row: VersionItem): string {
  if (row.op === 'create') return '初始新增'
  if (row.op === 'delete') return '快照为删除前状态'
  const d = diffMap.value.get(row.id) ?? []
  if (d.length === 0) return '回滚留痕（内容与上一版一致）'
  return `变更 ${d.length} 项：${d.map((e) => e.field).join('、')}`
}

async function rollback(row: VersionItem) {
  try {
    await ElMessageBox.confirm(
      `确定回滚到版本 ${row.version}（${OP_LABEL[row.op]}）吗？会把该内容恢复到此版本的状态，并再记一条版本。`,
      '确认回滚',
      { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await rollbackVersion(row.id)
    ElMessage.success(`已回滚到版本 ${row.version}`)
    await loadHistory()
  } catch {
    // client 已统一弹提示（如 70005）
  }
}

// 进场加载最近变更
void loadRecent()
</script>

<template>
  <div class="admin-page">
    <!-- ============================== 最近变更 ============================== -->
    <template v-if="mode === 'recent'">
      <PageHeader
        title="内容变更记录"
        subtitle="所有单元 / 词条 / 课文的变更都会留痕；点某条可看该内容的版本历史并回滚"
      />

      <el-card shadow="never" class="filter-card">
        <el-form :inline="true" @submit.prevent>
          <el-form-item label="内容类型">
            <el-select v-model="filterType" style="width: 130px" @change="onFilterChange">
              <el-option label="全部类型" value="" />
              <el-option label="单元" value="unit" />
              <el-option label="词条" value="word" />
              <el-option label="课文" value="text" />
            </el-select>
          </el-form-item>
          <el-form-item label="动作">
            <el-select v-model="filterOp" style="width: 130px" @change="onFilterChange">
              <el-option label="全部动作" value="" />
              <el-option label="新增" value="create" />
              <el-option label="修改" value="update" />
              <el-option label="停用" value="disable" />
              <el-option label="删除" value="delete" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button @click="onFilterChange">刷新</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <LoadingBox v-if="recentLoading" :loading="true" :has-data="false" :rows="8" class="table-wrap" />
      <EmptyState
        v-else-if="recentLoaded && recentItems.length === 0"
        description="暂无变更记录——对单元 / 词条 / 课文做过新增、修改、停用、删除后会出现在这里"
      />
      <el-card v-else shadow="never" class="table-wrap">
        <el-table :data="recentItems" stripe>
          <el-table-column label="记录时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="类型" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ TYPE_LABEL[row.entity_type as VersionEntityType] }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="内容" min-width="220">
            <template #default="{ row }">
              <div class="entity-cell">
                <span class="entity-name">{{ row.entity_name || `#${row.entity_id}` }}</span>
                <span class="entity-sub">{{ contextText(row) || '—' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="动作" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="OP_TAG[row.op as VersionOp]">
                {{ OP_LABEL[row.op as VersionOp] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作人" width="110" align="center">
            <template #default="{ row }">{{ row.operator_name || '—' }}</template>
          </el-table-column>
          <el-table-column prop="version" label="版本号" width="90" align="center" />
          <el-table-column label="操作" width="110" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="primary" @click="openHistory(row)">变更历史</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination
            layout="total, prev, pager, next"
            :total="recentTotal"
            :page-size="PAGE_SIZE"
            :current-page="recentPage"
            @current-change="onRecentPageChange"
          />
        </div>
      </el-card>
    </template>

    <!-- ============================== 版本历史 ============================== -->
    <template v-else>
      <PageHeader :title="`版本历史 · ${TYPE_LABEL[current.type]}`" :subtitle="historySubtitle">
        <template #before>
          <el-button @click="backToRecent">← 返回变更记录</el-button>
        </template>
      </PageHeader>

      <LoadingBox v-if="historyLoading" :loading="true" :has-data="false" :rows="8" class="table-wrap" />
      <EmptyState v-else-if="versions.length === 0" description="该内容暂无版本记录" />
      <el-card v-else shadow="never" class="table-wrap">
        <el-table :data="versions" stripe>
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="diff-box">
                <template v-if="(diffMap.get(row.id) ?? []).length > 0">
                  <div v-for="e in diffMap.get(row.id)" :key="e.field" class="diff-row">
                    <span class="diff-field">{{ e.field }}</span>
                    <span class="diff-from">{{ e.from }}</span>
                    <span class="diff-arrow">→</span>
                    <span class="diff-to">{{ e.to }}</span>
                  </div>
                </template>
                <span v-else class="diff-none">
                  {{ row.op === 'create' ? '初始新增（完整快照）' : '无字段差异（删除 / 回滚留痕）' }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本号" width="90" align="center" />
          <el-table-column label="动作" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="OP_TAG[row.op as VersionOp]">
                {{ OP_LABEL[row.op as VersionOp] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="变更内容" min-width="280">
            <template #default="{ row }">{{ diffSummary(row) }}</template>
          </el-table-column>
          <el-table-column label="操作人" width="110" align="center">
            <template #default="{ row }">{{ row.operator_name || '—' }}</template>
          </el-table-column>
          <el-table-column label="记录时间" width="170">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right" align="center">
            <template #default="{ row }">
              <el-button link type="warning" @click="rollback(row)">回滚到此版</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 16px;
}
.table-wrap {
  background: var(--admin-panel);
}
.entity-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.entity-name {
  font-weight: 600;
}
.entity-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.diff-box {
  padding: 4px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.diff-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
}
.diff-field {
  min-width: 76px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.diff-from {
  color: var(--el-text-color-secondary);
  text-decoration: line-through;
}
.diff-arrow {
  color: var(--el-text-color-secondary);
}
.diff-to {
  color: var(--el-color-success);
}
.diff-none {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
