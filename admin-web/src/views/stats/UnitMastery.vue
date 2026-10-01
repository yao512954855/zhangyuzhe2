<script setup lang="ts">
/**
 * 单元掌握度 —— WP-WEB-03 F8 / F13 / F14，验收 U9 / U14
 *
 * F8：正确率、已掌握比例、未完成学生列表、难词榜（**正确率升序**，低于 60% 标红）。
 * F14：无单元 / 无学生 / 无数据都要有引导性空状态。
 *
 * ⚠️ 数据来源：`GET /api/stats/unit/{unit_id}`（后端 `StatsService.unit_overview`）。
 * ⭐ 「未完成学生」这一块后端用的是 `v_student_unit_progress` 视图 + **外部的单元词条总数**比对，
 *    因为视图自带的 `word_total` 列口径不可靠（详见 `stats_service.py` 的 F7 例外说明）。
 *    未完成数 = 单元词条总数 − 该生已练词条数，所以页面展示「未完成 N 个」时用的是
 *    `unfinished_students[].missing_words`，不再自己减。
 *
 * ⚠️ 难点：单元下拉的候选来自 `GET /api/units`（管理端全量），而统计接口按 `unit_id` 取。
 *    两者 id 空间一致，直接透传。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import WordRateTable from '@/components/stats/WordRateTable.vue'
import { getUnitOverview, type UnitOverview } from '@/api/stats'
import { listUnitOptions } from '@/api/content'
import { formatPercent } from '@/utils/format'

const router = useRouter()

// ----------------------------- 单元下拉（管理端全量，含停用单元）
const unitOptions = ref<{ label: string; value: number }[]>([])
const selectedUnitId = ref<number | null>(null)

const SUBJECT_LABEL: Record<string, string> = { chinese: '语文', english: '英语' }
const SEMESTER_LABEL: Record<string, string> = { first: '上学期', second: '下学期' }

async function loadUnitOptions(): Promise<void> {
  try {
    const res = await listUnitOptions()
    unitOptions.value = res.items.map((u) => ({
      label: `${u.grade}年级${SEMESTER_LABEL[u.semester] ?? ''} · ${u.name}`,
      value: u.id,
    }))
    if (selectedUnitId.value === null && res.items.length > 0) {
      selectedUnitId.value = res.items[0].id
    }
  } catch {
    // 统一提示
  }
}

// ----------------------------- 数据
const loading = ref(false)
const loadedOnce = ref(false)
const overview = ref<UnitOverview | null>(null)

async function load(): Promise<void> {
  if (selectedUnitId.value === null) return
  loading.value = true
  try {
    overview.value = await getUnitOverview(selectedUnitId.value, { hard_word_limit: 50 })
    loadedOnce.value = true
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function onUnitChange(): void {
  overview.value = null
  loadedOnce.value = false
  load()
}

const unitIsEmpty = computed(
  () => loadedOnce.value && (overview.value?.student_count ?? 0) === 0,
)

/** 难词榜：后端已按正确率升序返回，这里再显式排一次，防上游口径变化导致 U9 失效。 */
const sortedHardWords = computed(() => {
  const list = overview.value?.hard_words ?? []
  return [...list].sort((a, b) => a.accuracy - b.accuracy)
})

onMounted(async () => {
  await loadUnitOptions()
  await load()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="单元掌握度"
      :subtitle="overview ? `${SUBJECT_LABEL[overview.subject] ?? overview.subject} · ${overview.unit_name}` : '数据看板'"
    >
      <el-button :loading="loading" @click="load">刷新</el-button>
      <el-button @click="router.push('/content/units')">去内容管理</el-button>
    </PageHeader>

    <el-card v-if="unitOptions.length === 0" shadow="never">
      <EmptyState description="还没有单元，先去内容管理建一个单元并导入词条">
        <el-button type="primary" @click="router.push('/content/units')">去内容管理</el-button>
      </EmptyState>
    </el-card>

    <template v-else>
      <el-card shadow="never" class="filter-card">
        <el-form :inline="true" @submit.prevent>
          <el-form-item label="单元">
            <el-select
              v-model="selectedUnitId"
              placeholder="选择单元"
              filterable
              style="width: 300px"
              @change="onUnitChange"
            >
              <el-option v-for="u in unitOptions" :key="u.value" :label="u.label" :value="u.value" />
            </el-select>
          </el-form-item>
        </el-form>
      </el-card>

      <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8">
        <el-card v-if="unitIsEmpty" shadow="never">
          <EmptyState description="还没有学生参与练习，暂无掌握度数据">
            <el-button type="primary" @click="router.push('/students/import')">导入学生</el-button>
          </EmptyState>
        </el-card>

        <template v-else-if="overview">
          <div class="card-grid">
            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">单元词条</div>
              <div class="stat-card__value">
                {{ overview.word_total }}<span class="stat-card__unit">词</span>
              </div>
              <div class="stat-card__foot">参与学生 {{ overview.student_count }} 人</div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">正确率</div>
              <div class="stat-card__value">{{ formatPercent(overview.accuracy, true) }}</div>
              <div class="stat-card__foot">
                计分 {{ overview.correct_count }}/{{ overview.counted_count }} 题
              </div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">已掌握比例</div>
              <div class="stat-card__value">{{ formatPercent(overview.mastered_ratio, true) }}</div>
              <div class="stat-card__foot">
                已掌握 {{ overview.mastered_word_count }}/{{ overview.word_total }} 词
              </div>
            </el-card>

            <el-card shadow="never" class="stat-card">
              <div class="stat-card__label">覆盖进度</div>
              <div class="stat-card__value">{{ formatPercent(overview.completion_ratio, true) }}</div>
              <div class="stat-card__foot">
                已练 {{ overview.practiced_word_count }}/{{ overview.word_total }} 词
              </div>
            </el-card>
          </div>

          <el-alert type="info" :closable="false" class="calibre-tip">
            <template #title>
              口径说明：正确率已排除「识别为空」的题；「已掌握」= 该词条的掌握状态达到要求的学生-词条对
              （共 {{ overview.mastered_pair_count }} 对）；「未完成」= 单元词条总数 − 该生已练词条数。
            </template>
          </el-alert>

          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>难词榜（正确率升序，低于 60% 标红）</span>
                <span class="block-card__hint">共 {{ sortedHardWords.length }} 条</span>
              </div>
            </template>
            <WordRateTable :words="sortedHardWords" />
          </el-card>

          <el-card shadow="never" class="block-card">
            <template #header>
              <div class="block-card__header">
                <span>未完成学生</span>
                <span class="block-card__hint">按未完成词条数降序</span>
              </div>
            </template>
            <el-table
              :data="overview.unfinished_students"
              border
              stripe
              size="small"
              empty-text="所有学生都已练完本单元"
            >
              <el-table-column prop="username" label="学号" width="130" />
              <el-table-column prop="real_name" label="姓名" width="110" />
              <el-table-column label="已练词条" width="100" align="center">
                <template #default="{ row }">{{ row.practiced_words }}</template>
              </el-table-column>
              <el-table-column label="已掌握" width="100" align="center">
                <template #default="{ row }">{{ row.mastered_words }}</template>
              </el-table-column>
              <el-table-column label="未完成" width="100" align="center">
                <template #default="{ row }">
                  <span class="missing-count">{{ row.missing_words }}</span>
                </template>
              </el-table-column>
              <el-table-column label="正确率" width="110" align="center">
                <template #default="{ row }">{{ formatPercent(row.accuracy, true) }}</template>
              </el-table-column>
              <el-table-column label="操作" min-width="110">
                <template #default="{ row }">
                  <el-button link type="primary" @click="router.push(`/stats/student?student_id=${row.student_id}`)">
                    报告
                  </el-button>
                </template>
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
.missing-count {
  color: #e6a23c;
  font-weight: 600;
}
</style>
