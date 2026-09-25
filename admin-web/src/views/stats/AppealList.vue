<script setup lang="ts">
/**
 * 申诉复核 —— WP-WEB-03 F10，验收 U11
 *
 * F10：列表（默认 `pending`）+ 详情（题目、标准答案、识别文本、匹配类型）+ 复核操作
 *      （成立 / 驳回 + 备注）；**页面必须显式说明「复核不改判分，仅作质量数据」**。
 *
 * ⚠️ 硬约束（契约 §8.9 + `app/api/appeals.py` 模块 docstring）：
 *     复核**只写** `answers.appeal_result` 与 `audit_logs`，**绝不修改 `is_correct`**。
 *     所以复核后「判定」列的标签不会变 —— 这不是 bug，是设计。页面上要写清楚，
 *     否则管理者会以为「我点了成立，怎么判分没改」。
 *
 * ⚠️ 备注长度：后端 `AppealReviewIn.remark` 限 200 字（超出会 10001），
 *     前端 `maxlength=200` + `show-word-limit` 双保险。
 *
 * ⚠️ 录音：一期不保存录音，`audio_available` 恒为 `false`。页面上如实说明「一期不支持试听」，
 *     不要放一个点下去没反应的播放器。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { listAppeals, reviewAppeal, type AppealItem, type AppealResult } from '@/api/stats'
import { formatDateTime, formatPercent } from '@/utils/format'

const router = useRouter()

const MATCH_LABEL: Record<string, string> = {
  exact: '完全一致',
  loose: '宽松一致',
  fuzzy: '模糊一致',
  wrong: '不一致',
  empty: '识别为空',
  missing: '漏字',
  extra: '多余',
}

const STATUS_TABS: { label: string; value: 'pending' | 'upheld' | 'rejected' | 'all' }[] = [
  { label: '待复核', value: 'pending' },
  { label: '已成立', value: 'upheld' },
  { label: '已驳回', value: 'rejected' },
  { label: '全部', value: 'all' },
]

// ----------------------------- 列表
const filters = reactive<{ status: 'pending' | 'upheld' | 'rejected' | 'all' }>({ status: 'pending' })
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const loadedOnce = ref(false)
const items = ref<AppealItem[]>([])

async function load(): Promise<void> {
  loading.value = true
  try {
    const res = await listAppeals({
      status: filters.status,
      page: page.value,
      pageSize: pageSize.value,
    })
    items.value = res.items
    total.value = res.total
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function onTabChange(): void {
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

// ----------------------------- 详情
const detailVisible = ref(false)
const detail = ref<AppealItem | null>(null)

function openDetail(row: AppealItem): void {
  detail.value = row
  detailVisible.value = true
}

/** 详情里展示的「差异」：字母序列对不上时，把两串并排给管理者看。 */
const detailDiff = computed(() => {
  const d = detail.value
  if (!d) return null
  if (!d.letter_seq && !d.target_seq) return null
  return { heard: d.letter_seq || '—', expected: d.target_seq || '—' }
})

// ----------------------------- 复核
const reviewing = ref(false)
const remark = ref('')
const pendingResult = ref<AppealResult | null>(null)

/**
 * 发起一次复核。**必须二次确认** —— 这是会写审计的对外动作，
 * 且页面上已经写明「不改判分」，避免误操作。
 */
async function submitReview(result: AppealResult): Promise<void> {
  const row = detail.value
  if (!row) return
  const verb = result === 'upheld' ? '申诉成立' : '维持原判（驳回）'
  try {
    await ElMessageBox.confirm(
      `确认本次复核结论为「${verb}」？\n\n注意：复核只记录质量数据，不会修改该题的判分结果。`,
      '确认复核',
      { type: 'warning', confirmButtonText: '确认提交', cancelButtonText: '再想想' },
    )
  } catch {
    return
  }

  pendingResult.value = result
  reviewing.value = true
  try {
    const res = await reviewAppeal(row.id, {
      result,
      ...(remark.value.trim() ? { remark: remark.value.trim() } : {}),
    })
    ElMessage.success(res.message || '复核已记录')
    detailVisible.value = false
    remark.value = ''
    await load()
  } catch {
    // 统一提示
  } finally {
    reviewing.value = false
    pendingResult.value = null
  }
}

function statusTagType(r: string | null): 'success' | 'danger' | 'info' {
  if (r === 'upheld') return 'success'
  if (r === 'rejected') return 'danger'
  return 'info'
}

const REVIEW_LABEL: Record<string, string> = { upheld: '申诉成立', rejected: '维持原判' }

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="申诉复核"
      :subtitle="loadedOnce ? `共 ${total} 条（${STATUS_TABS.find((t) => t.value === filters.status)?.label}）` : '申诉复核'"
    >
      <el-button :loading="loading" @click="load">刷新</el-button>
    </PageHeader>

    <!-- 口径说明：必须显眼（F10 硬要求） -->
    <el-alert type="warning" :closable="false" class="calibre-tip" show-icon>
      <template #title>
        复核<b>只作质量数据，不改判分</b>：无论选择「成立」还是「驳回」，该题原始对错与学生的成绩都不会变化。
        本页记录用于回看识别质量、优化判分阈值，不用于改分。
      </template>
    </el-alert>

    <el-card shadow="never" class="filter-card">
      <el-radio-group v-model="filters.status" @change="onTabChange">
        <el-radio-button v-for="t in STATUS_TABS" :key="t.value" :value="t.value">
          {{ t.label }}
        </el-radio-button>
      </el-radio-group>
    </el-card>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6">
      <EmptyState
        v-if="loadedOnce && items.length === 0"
        :description="
          filters.status === 'pending'
            ? '当前没有待复核的申诉，学生申诉后会出现在这里'
            : '这个状态下没有申诉记录'
        "
      >
        <el-button @click="router.push('/stats/class')">去看班级数据</el-button>
      </EmptyState>

      <template v-else>
        <el-table :data="items" border stripe size="small">
          <el-table-column prop="id" label="记录号" width="80" align="center" />
          <el-table-column label="学生" width="150">
            <template #default="{ row }">
              {{ row.real_name }}<span class="dim">（{{ row.username }}）</span>
            </template>
          </el-table-column>
          <el-table-column label="班级" width="130">
            <template #default="{ row }">{{ row.class_name || '未分班' }}</template>
          </el-table-column>
          <el-table-column label="题目" min-width="180">
            <template #default="{ row }">
              <span v-if="row.en">{{ row.en }}<span class="dim"> / {{ row.cn }}</span></span>
              <span v-else class="dim">{{ row.expected || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="标准答案" width="120">
            <template #default="{ row }">{{ row.expected || '—' }}</template>
          </el-table-column>
          <el-table-column label="识别文本" min-width="140">
            <template #default="{ row }">{{ row.asr_text || '—' }}</template>
          </el-table-column>
          <el-table-column label="匹配类型" width="100" align="center">
            <template #default="{ row }">
              {{ MATCH_LABEL[row.match_type] ?? row.match_type }}
            </template>
          </el-table-column>
          <el-table-column label="判定" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.is_correct ? 'success' : 'danger'" size="small">
                {{ row.is_correct ? '正确' : '错误' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="复核结论" width="110" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.appeal_result" :type="statusTagType(row.appeal_result)" size="small">
                {{ REVIEW_LABEL[row.appeal_result] ?? row.appeal_result }}
              </el-tag>
              <span v-else class="dim">待处理</span>
            </template>
          </el-table-column>
          <el-table-column label="申诉时间" width="140">
            <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDetail(row)">详情</el-button>
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

    <!-- 详情 + 复核 -->
    <el-dialog v-model="detailVisible" title="申诉详情" width="640px">
      <template v-if="detail">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="学生">
            {{ detail.real_name }}（{{ detail.username }}）
          </el-descriptions-item>
          <el-descriptions-item label="班级">{{ detail.class_name || '未分班' }}</el-descriptions-item>
          <el-descriptions-item label="题目">{{ detail.en || detail.expected || '—' }}</el-descriptions-item>
          <el-descriptions-item label="中文">{{ detail.cn || '—' }}</el-descriptions-item>
          <el-descriptions-item label="标准答案">{{ detail.expected || '—' }}</el-descriptions-item>
          <el-descriptions-item label="识别文本">{{ detail.asr_text || '—' }}</el-descriptions-item>
          <el-descriptions-item label="匹配类型">
            {{ MATCH_LABEL[detail.match_type] ?? detail.match_type }}
          </el-descriptions-item>
          <el-descriptions-item label="当前判定">
            <el-tag :type="detail.is_correct ? 'success' : 'danger'" size="small">
              {{ detail.is_correct ? '正确' : '错误' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="识别置信度">
            {{ formatPercent(detail.asr_confidence, true) }}
          </el-descriptions-item>
          <el-descriptions-item label="作答用时">
            {{ detail.duration_ms == null ? '—' : `${detail.duration_ms} ms` }}
          </el-descriptions-item>
          <el-descriptions-item label="重试次数">{{ detail.retry_count ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="用了键盘">
            {{ detail.used_keyboard ? '是（已降级）' : '否' }}
          </el-descriptions-item>
          <el-descriptions-item label="识别服务">{{ detail.provider || '—' }}</el-descriptions-item>
          <el-descriptions-item label="申诉时间">
            {{ formatDateTime(detail.created_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <div v-if="detailDiff" class="diff-block">
          <div class="diff-row">
            <span class="diff-label">学生念出（字母序列）</span>
            <span class="diff-mono">{{ detailDiff.heard }}</span>
          </div>
          <div class="diff-row">
            <span class="diff-label">应该念出（字母序列）</span>
            <span class="diff-mono">{{ detailDiff.expected }}</span>
          </div>
        </div>

        <el-alert
          type="info"
          :closable="false"
          class="calibre-tip"
          title="一期不保存录音，无法在此试听。判定依据为上方识别文本与序列对比。"
        />

        <el-alert
          v-if="detail.appeal_result"
          type="success"
          :closable="false"
          class="calibre-tip"
          :title="`本条已复核：${REVIEW_LABEL[detail.appeal_result] ?? detail.appeal_result}${detail.review_remark ? ' · 备注：' + detail.review_remark : ''}`"
        />

        <el-form label-width="60px" class="remark-form">
          <el-form-item label="备注">
            <el-input
              v-model="remark"
              type="textarea"
              :rows="2"
              maxlength="200"
              show-word-limit
              placeholder="可选，≤200 字。会写入审计日志，用于说明本次复核的判断依据。"
            />
          </el-form-item>
        </el-form>
      </template>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          :loading="reviewing && pendingResult === 'rejected'"
          @click="submitReview('rejected')"
        >
          维持原判（驳回）
        </el-button>
        <el-button
          type="primary"
          :loading="reviewing && pendingResult === 'upheld'"
          @click="submitReview('upheld')"
        >
          申诉成立
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.calibre-tip {
  margin-bottom: 12px;
}
.filter-card {
  margin-bottom: 16px;
}
.dim {
  color: var(--admin-text-secondary);
  font-size: 12px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0;
}
.diff-block {
  margin: 12px 0;
  padding: 10px 12px;
  border: 1px solid var(--admin-border);
  border-radius: 6px;
  background: var(--admin-bg);
}
.diff-row {
  display: flex;
  gap: 10px;
  padding: 3px 0;
  font-size: 13px;
}
.diff-label {
  flex: 0 0 160px;
  color: var(--admin-text-secondary);
}
.diff-mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  word-break: break-all;
}
.remark-form {
  margin-top: 8px;
}
</style>
