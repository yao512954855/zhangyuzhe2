<script setup lang="ts">
/**
 * 学生个人报告 —— WP-WEB-03 F9，验收 U10
 *
 * F9：概要卡片 + 最近场次表 + 背诵记录表 + 错词表；可切换学生。
 *
 * 入口有三种（都靠 query 传参）：
 *   - `/stats/student`             → 空手进来，自己选
 *   - `/stats/student?student_id=7` → 学生管理 / 单元掌握度 点「报告」跳过来
 *   - `/stats/student?class_id=3`   → 班级概览点「查看学生报告」，带班筛选
 *
 * ⚠️ 关键：**query 变化要重新拉数据**。如果只在 `onMounted` 读一次 query，
 * 那从 A 学生的报告点「报告」跳到 B 学生时页面不会刷新（同一路由，组件复用），
 * 表现为「点了没反应」。这里用 `watch(() => route.query)` 兜住。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { exportStats, getStudentReport, type StudentReport } from '@/api/stats'
import { listStudents, type StudentItem } from '@/api/students'
import { formatDateTime, formatDuration, formatPercent } from '@/utils/format'

const route = useRoute()
const router = useRouter()

/** 把 Blob 存成文件。页面只有一个下载点，就地写，不额外引入跨包依赖。 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

const STATUS_LABEL: Record<string, string> = { active: '正常', disabled: '停用' }
const SOURCE_LABEL: Record<string, string> = { import: '导入', self: '自主注册' }
const CONTENT_LABEL: Record<string, string> = { word: '词条练习', text: '课文背诵' }
const STRATEGY_LABEL: Record<string, string> = {
  sequence: '顺序',
  random: '随机',
  wrong: '只练错题',
}
const FINISH_LABEL: Record<string, string> = {
  manual: '主动结束',
  timeout: '限时到点',
  completed: '背诵完成',
}

// ----------------------------- 学生下拉
const studentOptions = ref<{ label: string; value: number }[]>([])
const selectedStudentId = ref<number | null>(null)
const classFilterId = ref<number | null>(null)

async function loadStudentOptions(): Promise<void> {
  try {
    const res = await listStudents({
      page: 1,
      pageSize: 200,
      ...(classFilterId.value !== null ? { classId: classFilterId.value } : {}),
    })
    studentOptions.value = res.items.map((s: StudentItem) => ({
      label: `${s.real_name}（${s.username}${s.class_name ? ' · ' + s.class_name : ' · 未分班'}）`,
      value: s.id,
    }))
  } catch {
    // 统一提示
  }
}

// ----------------------------- 数据
const loading = ref(false)
const loadedOnce = ref(false)
const report = ref<StudentReport | null>(null)
const exporting = ref(false)

async function load(): Promise<void> {
  if (selectedStudentId.value === null) {
    report.value = null
    loadedOnce.value = true
    return
  }
  loading.value = true
  try {
    report.value = await getStudentReport(selectedStudentId.value, { recent_limit: 20 })
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function onStudentChange(): void {
  // 换人时把 id 同步到地址栏，刷新页面不丢上下文
  const query = selectedStudentId.value === null ? {} : { student_id: String(selectedStudentId.value) }
  router.replace({ path: '/stats/student', query })
  load()
}

/** 空状态判定：选了人但一个字都没练过。 */
const noPractice = computed(
  () => !!report.value && report.value.answer_count === 0 && report.value.recitation_count === 0,
)

async function onExport(): Promise<void> {
  if (selectedStudentId.value === null) return
  exporting.value = true
  try {
    const blob = await exportStats('student', selectedStudentId.value)
    saveBlob(blob, `student_${selectedStudentId.value}_report.xlsx`)
    ElMessage.success('导出已开始下载')
  } catch {
    // 统一提示
  } finally {
    exporting.value = false
  }
}

// ----------------------------- query 驱动（支持从别的页面跳进来 / 换人）
watch(
  () => route.query,
  async (q) => {
    const sid = q.student_id ? Number(q.student_id) : null
    const cid = q.class_id ? Number(q.class_id) : null
    const classChanged = cid !== classFilterId.value
    classFilterId.value = cid
    if (classChanged) {
      await loadStudentOptions()
    }
    if (sid && sid !== selectedStudentId.value) {
      selectedStudentId.value = sid
    } else if (!sid && q.class_id) {
      // 只带班过来：默认落在该班第一个学生，避免空白页
      selectedStudentId.value = studentOptions.value[0]?.value ?? null
    }
    await load()
  },
  { immediate: true },
)

onMounted(() => {
  // 初次由上面的 immediate watch 负责拉数据；这里只保证没有 query 时也有下拉可选
  if (studentOptions.value.length === 0) void loadStudentOptions()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="学生报告"
      :subtitle="report ? `${report.real_name}（${report.username}）· ${report.class_name || '未分班'}` : '选择一名学生查看报告'"
    >
      <el-button :loading="exporting" :disabled="!report" @click="onExport">导出 Excel</el-button>
      <el-button :loading="loading" :disabled="selectedStudentId === null" @click="load">刷新</el-button>
    </PageHeader>

    <el-card v-if="studentOptions.length === 0" shadow="never">
      <EmptyState description="还没有学生，先导入名单或让学生自主注册">
        <el-button type="primary" @click="router.push('/students/import')">导入学生</el-button>
        <el-button @click="router.push('/students/list')">去学生管理</el-button>
      </EmptyState>
    </el-card>

    <template v-else>
      <el-card shadow="never" class="filter-card">
        <el-form :inline="true" @submit.prevent>
          <el-form-item label="学生">
            <el-select
              v-model="selectedStudentId"
              placeholder="选择学生（可输入姓名或学号搜索）"
              filterable
              style="width: 320px"
              @change="onStudentChange"
            >
              <el-option v-for="s in studentOptions" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="classFilterId">
            <el-tag type="info" closable @close="router.replace({ path: '/stats/student' })">
              已按班级筛选
            </el-tag>
          </el-form-item>
        </el-form>
      </el-card>

      <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8">
        <EmptyState v-if="!report" description="请先选择一名学生" />

        <template v-else>
          <!-- 概要卡片 -->
          <div class="card-grid">
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">正确率</div>
              <div class="stat-card__value">{{ formatPercent(report.accuracy, true) }}</div>
              <div class="stat-card__foot">
                计分 {{ report.correct_count }}/{{ report.counted_count }} 题
              </div>
            </el-card>
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">已掌握词条</div>
              <div class="stat-card__value">
                {{ report.mastered_words }}<span class="stat-card__unit">词</span>
              </div>
              <div class="stat-card__foot">累计答题 {{ report.answer_count }} 题</div>
            </el-card>
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">练习场次</div>
              <div class="stat-card__value">
                {{ report.session_count }}<span class="stat-card__unit">场</span>
              </div>
              <div class="stat-card__foot">已完成 {{ report.finished_session_count }} 场</div>
            </el-card>
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">背诵记录</div>
              <div class="stat-card__value">
                {{ report.recitation_count }}<span class="stat-card__unit">次</span>
              </div>
              <div class="stat-card__foot">练习天数 {{ report.practice_days }} 天</div>
            </el-card>
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">连续练习</div>
              <div class="stat-card__value">
                {{ report.streak_days }}<span class="stat-card__unit">天</span>
              </div>
              <div class="stat-card__foot">最后登录 {{ formatDateTime(report.last_login_at) }}</div>
            </el-card>
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">账号</div>
              <div class="stat-card__value stat-card__value--sm">
                {{ STATUS_LABEL[report.status] ?? report.status }}
              </div>
              <div class="stat-card__foot">
                来源 {{ SOURCE_LABEL[report.register_source] ?? report.register_source }}
              </div>
            </el-card>
          </div>

          <el-alert type="info" :closable="false" class="calibre-tip">
            <template #title>
              口径说明：正确率已排除「识别为空」的题；「已掌握」按学生-词条掌握状态统计，
              与词条总数不是同一维度。
            </template>
          </el-alert>

          <el-alert
            v-if="noPractice"
            type="warning"
            :closable="false"
            class="calibre-tip"
            title="该学生还没有任何练习或背诵记录。"
          />

          <!-- 最近场次 -->
          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>最近练习场次</span>
                <span class="block-card__hint">按开始时间倒序，最多 20 条</span>
              </div>
            </template>
            <el-table
              :data="report.recent_sessions"
              border
              stripe
              size="small"
              empty-text="暂无练习场次"
            >
              <el-table-column prop="session_id" label="场次" width="80" align="center" />
              <el-table-column label="内容" width="110" align="center">
                <template #default="{ row }">
                  {{ CONTENT_LABEL[row.content_type] ?? row.content_type }}
                </template>
              </el-table-column>
              <el-table-column label="单元" min-width="150">
                <template #default="{ row }">{{ row.unit_name || '—' }}</template>
              </el-table-column>
              <el-table-column label="策略" width="100" align="center">
                <template #default="{ row }">
                  {{ STRATEGY_LABEL[row.strategy ?? ''] ?? (row.strategy || '—') }}
                </template>
              </el-table-column>
              <el-table-column label="题数" width="90" align="center">
                <template #default="{ row }">
                  {{ row.correct_items }}/{{ row.total_items }}
                </template>
              </el-table-column>
              <el-table-column prop="hint_count" label="提示" width="80" align="center" />
              <el-table-column label="状态" width="100" align="center">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'finished' ? 'success' : 'info'" size="small">
                    {{ row.status === 'finished' ? '已结束' : '进行中' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="开始时间" width="140">
                <template #default="{ row }">{{ formatDateTime(row.started_at) }}</template>
              </el-table-column>
              <el-table-column label="结束时间" width="140">
                <template #default="{ row }">{{ formatDateTime(row.ended_at) }}</template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 背诵记录 -->
          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>背诵记录</span>
                <span class="block-card__hint">按时间倒序</span>
              </div>
            </template>
            <el-table
              :data="report.recitations"
              border
              stripe
              size="small"
              empty-text="暂无背诵记录"
            >
              <el-table-column prop="text_title" label="课文" min-width="180">
                <template #default="{ row }">{{ row.text_title || '—' }}</template>
              </el-table-column>
              <el-table-column label="已背/总字数" width="130" align="center">
                <template #default="{ row }">{{ row.matched_chars }}/{{ row.total_chars }}</template>
              </el-table-column>
              <el-table-column label="覆盖率" width="100" align="center">
                <template #default="{ row }">
                  {{ formatPercent(row.covered_ratio, true) }}
                </template>
              </el-table-column>
              <el-table-column label="得分" width="90" align="center">
                <template #default="{ row }">{{ row.score ?? '—' }}</template>
              </el-table-column>
              <el-table-column label="用时" width="100" align="center">
                <template #default="{ row }">{{ formatDuration(row.duration_ms) }}</template>
              </el-table-column>
              <el-table-column label="首次发声" width="110" align="center">
                <template #default="{ row }">{{ formatDuration(row.first_voice_ms) }}</template>
              </el-table-column>
              <el-table-column label="结束方式" width="110" align="center">
                <template #default="{ row }">
                  {{ FINISH_LABEL[row.finish_reason ?? ''] ?? (row.finish_reason || '—') }}
                </template>
              </el-table-column>
              <el-table-column label="重开始" width="90" align="center">
                <template #default="{ row }">{{ row.restart_count }}</template>
              </el-table-column>
              <el-table-column label="时间" width="140">
                <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- 错词 -->
          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>错词表</span>
                <span class="block-card__hint">
                  共 {{ report.wrong_word_total }} 个，下面列出前 {{ report.wrong_words.length }} 个（按错误次数降序）
                </span>
              </div>
            </template>
            <el-table
              :data="report.wrong_words"
              border
              stripe
              size="small"
              empty-text="还没有答错的词条"
            >
              <el-table-column prop="en" label="英文" min-width="130" />
              <el-table-column prop="cn" label="中文" min-width="130" />
              <el-table-column label="单元" min-width="150">
                <template #default="{ row }">{{ row.unit_name || '—' }}</template>
              </el-table-column>
              <el-table-column prop="answer_count" label="答题数" width="90" align="center" />
              <el-table-column label="错误数" width="90" align="center">
                <template #default="{ row }">
                  <span class="err-count">{{ row.error_count }}</span>
                </template>
              </el-table-column>
              <el-table-column label="正确率" width="110" align="center">
                <template #default="{ row }">{{ formatPercent(row.accuracy, true) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </template>
      </LoadingBox>
    </template>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 16px;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card__label {
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.stat-card__value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-card__value--sm {
  font-size: 20px;
}
.stat-card__unit {
  margin-left: 4px;
  font-size: 14px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}
.stat-card__foot {
  margin-top: 6px;
  font-size: 12px;
  color: var(--admin-text-secondary);
}
.calibre-tip {
  margin-bottom: 12px;
}
.block-card {
  margin-bottom: 16px;
}
.block-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}
.block-card__hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}
.err-count {
  color: #f56c6c;
  font-weight: 600;
}
</style>
