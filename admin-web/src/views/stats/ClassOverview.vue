<script setup lang="ts">
/**
 * 班级概览 —— WP-WEB-03 F7 / F13 / F14，验收 U7 / U8 / U14
 *
 * F7：卡片区（人数 / 今日活跃 / 完成率 / 平均正确率 / 平均用时）+ 趋势折线图 + 错题 Top 榜；
 *     日期范围默认近 7 天，可切换近 30 天（U8：趋势点数随之变化）。
 * F13：趋势图用 ECharts（`components/stats/TrendChart.vue`），中文标签、百分比带单位。
 * F14：无班级 / 无学生 / 无数据都要有引导性空状态与主操作按钮。
 *
 * ⚠️ 口径标注（规格「关键交互要求」）：所有数字均由后端 `StatsService` 给出，
 * 页面**不做任何重算**（契约 §13 禁止清单 #8）。卡片下方显式标注两个容易误读的口径：
 *   1. 正确率 = (exact+loose+fuzzy) / (总数 − 识别为空)，识别为空的题**不计入分母**；
 *   2. 完成率按**词条维度**统计（class 年级 + 学期范围内的词条）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import TrendChart from '@/components/stats/TrendChart.vue'
import { getClassOverview, type ClassOverview } from '@/api/stats'
import { listClassOptions } from '@/api/students'
import { formatDuration, formatPercent } from '@/utils/format'

const router = useRouter()

// ----------------------------- 班级下拉
const classOptions = ref<{ label: string; value: number }[]>([])
const selectedClassId = ref<number | null>(null)

async function loadClassOptions(): Promise<void> {
  try {
    const res = await listClassOptions({ status: 'all' })
    classOptions.value = res.items.map((c) => ({ label: c.name, value: c.id }))
    if (selectedClassId.value === null && res.items.length > 0) {
      selectedClassId.value = res.items[0].id
    }
  } catch {
    // 统一提示
  }
}

// ----------------------------- 日期范围（默认近 7 天，含今天）
function isoDay(offsetDays: number): string {
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

const dateRange = ref<[string, string]>([isoDay(-6), isoDay(0)])

const RANGE_PRESETS: { label: string; days: number }[] = [
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
]

function applyPreset(days: number): void {
  dateRange.value = [isoDay(-(days - 1)), isoDay(0)]
  load()
}

// ----------------------------- 数据
const loading = ref(false)
const loadedOnce = ref(false)
const overview = ref<ClassOverview | null>(null)

async function load(): Promise<void> {
  if (selectedClassId.value === null) return
  loading.value = true
  try {
    const [from, to] = dateRange.value
    overview.value = await getClassOverview(selectedClassId.value, {
      date_from: from,
      date_to: to,
      top_n: 10,
    })
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function onClassChange(): void {
  overview.value = null
  loadedOnce.value = false
  load()
}

/** U8：趋势天数 —— 用来断言「切换日期范围后聚合结果真的变了」。 */
const trendDays = computed(() => overview.value?.trend?.length ?? 0)

/** 该班是否一个人都没有（U14 引导性空状态）。 */
const classIsEmpty = computed(
  () => loadedOnce.value && (overview.value?.student_count ?? 0) === 0,
)

/** 有学生但窗口内没有任何答题（另一种「无数据」，给不同引导文案）。 */
const noActivity = computed(
  () => loadedOnce.value && !classIsEmpty.value && (overview.value?.answer_count ?? 0) === 0,
)

onMounted(async () => {
  await loadClassOptions()
  await load()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="班级概览"
      :subtitle="overview ? `${overview.class_name} · ${overview.date_from} ~ ${overview.date_to}` : '数据看板'"
    >
      <el-button :loading="loading" @click="load">刷新</el-button>
      <el-button
        type="primary"
        :disabled="selectedClassId === null"
        @click="selectedClassId && router.push(`/stats/student?class_id=${selectedClassId}`)"
      >
        查看学生报告
      </el-button>
    </PageHeader>

    <!-- 没有班级：引导去建班（U14） -->
    <el-card v-if="classOptions.length === 0" shadow="never">
      <EmptyState description="还没有班级，先创建一个班级再来看数据">
        <el-button type="primary" @click="router.push('/students/classes')">去建班</el-button>
      </EmptyState>
    </el-card>

    <template v-else>
      <el-card shadow="never" class="filter-card">
        <el-form :inline="true" @submit.prevent>
          <el-form-item label="班级">
            <el-select
              v-model="selectedClassId"
              placeholder="选择班级"
              style="width: 180px"
              @change="onClassChange"
            >
              <el-option v-for="c in classOptions" :key="c.value" :label="c.label" :value="c.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="日期范围">
            <el-date-picker
              v-model="dateRange"
              type="daterange"
              value-format="YYYY-MM-DD"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              :clearable="false"
              style="width: 260px"
              @change="load"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              v-for="p in RANGE_PRESETS"
              :key="p.label"
              :type="trendDays === p.days ? 'primary' : 'default'"
              size="small"
              @click="applyPreset(p.days)"
            >
              {{ p.label }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8">
        <!-- U14：班里一个学生都没有 -->
        <el-card v-if="classIsEmpty" shadow="never">
          <EmptyState description="该班级还没有学生，导入名单后就能看到数据了">
            <el-button type="primary" @click="router.push('/students/import')">导入学生</el-button>
            <el-button @click="router.push(`/students/list`)">去学生管理</el-button>
          </EmptyState>
        </el-card>

        <template v-else-if="overview">
          <!-- 卡片区（F7 五张） -->
          <div class="card-grid">
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">班级人数</div>
              <div class="stat-card__value">
                {{ overview.student_count }}<span class="stat-card__unit">人</span>
              </div>
              <div class="stat-card__foot">其中在读 {{ overview.active_student_count }} 人</div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">今日活跃</div>
              <div class="stat-card__value">
                {{ overview.today_active_students }}<span class="stat-card__unit">人</span>
              </div>
              <div class="stat-card__foot">
                {{ overview.today_date }} · {{ overview.today_item_count }} 题
              </div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">完成率</div>
              <div class="stat-card__value">{{ formatPercent(overview.completion_ratio, true) }}</div>
              <div class="stat-card__foot">
                词条维度 {{ overview.practiced_word_count }}/{{ overview.scope_word_count }}
              </div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">平均正确率</div>
              <div class="stat-card__value">{{ formatPercent(overview.accuracy, true) }}</div>
              <div class="stat-card__foot">
                计分 {{ overview.correct_count }}/{{ overview.counted_count }} 题
              </div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">平均用时</div>
              <div class="stat-card__value stat-card__value--sm">
                {{ formatDuration(overview.avg_session_ms) }}
              </div>
              <div class="stat-card__foot">
                单题均 {{ formatDuration(overview.avg_answer_ms) }}
              </div>
            </el-card>
          </div>

          <el-alert type="info" :closable="false" class="calibre-tip">
            <template #title>
              口径说明：正确率 = (完全一致 + 宽松一致 + 模糊一致) ÷ (答题总数 − 识别为空)，
              <b>识别为空（没说话）的题不计入分母</b>；完成率按<b>词条维度</b>统计；
              「平均用时」为场内用时均值。全部数字由后端统一计算，页面不做二次加工。
            </template>
          </el-alert>

          <!-- 无答题数据的提示（不遮住卡片，只是加一条引导） -->
          <el-alert
            v-if="noActivity"
            type="warning"
            :closable="false"
            class="calibre-tip"
            title="所选时间范围内还没有答题记录，可以换一个日期范围或等学生先练一轮。"
          />

          <!-- 趋势（F7 / F13） -->
          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>答题趋势（{{ trendDays }} 天）</span>
                <span class="block-card__hint">活跃人数 / 答题数 / 正确率</span>
              </div>
            </template>
            <TrendChart :trend="overview.trend" />
          </el-card>

          <!-- 错题 Top 榜 -->
          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>错题 Top {{ overview.top_wrong_words.length }}</span>
                <span class="block-card__hint">按错误次数降序（{{ overview.date_from }} ~ {{ overview.date_to }}）</span>
              </div>
            </template>
            <el-table
              :data="overview.top_wrong_words"
              border
              stripe
              size="small"
              empty-text="这段时间没有错题"
            >
              <el-table-column type="index" label="#" width="52" align="center" />
              <el-table-column prop="en" label="英文" min-width="130" />
              <el-table-column prop="cn" label="中文" min-width="130" />
              <el-table-column label="所属单元" min-width="150">
                <template #default="{ row }">{{ row.unit_name || '—' }}</template>
              </el-table-column>
              <el-table-column label="错误次数" width="100" align="center">
                <template #default="{ row }">
                  <span class="err-count">{{ row.error_count }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="student_count" label="涉及人数" width="100" align="center" />
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
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.stat-card__label {
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.stat-card__value {
  margin-top: 6px;
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-card__value--sm {
  font-size: 24px;
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
