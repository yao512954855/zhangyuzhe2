<script setup lang="ts">
/**
 * 课堂实时大屏 —— WP-2D（二期 G1），验收 U1 ~ U3
 *
 * 数据来自 `GET /api/stats/live-board`（admin 鉴权，可选 `class_id`）。
 * 实时刷新走**轮询**（一期不强制 WebSocket，契约 §15 / 50-文档 §3）。
 *
 * ⚠️ 口径（契约 §13 禁止清单 #8 / §15.5）：所有数字（含正确率）由后端
 * `UserService._accuracy` 统一给出，页面**绝不重算**——这里只用 `formatPercent` 做展示格式化。
 * ⚠️ 请求**只走** `api/client.ts` 的 `api`（统一管理 token / 解包 / 错误提示），
 * 页面里不手搓 axios。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { api } from '@/api/client'
import { listClasses, type ClassItem } from '@/api/students'
import { formatDateTime, formatPercent } from '@/utils/format'

interface LiveClass {
  class_id: number
  class_name: string
  grade: number
  semester: string
  school_year: string | null
  status: string
  student_count: number
  active_student_count: number
  today_active_students: number
  today_session_count: number
  today_item_count: number
  today_accuracy: number
}

interface LiveStudent {
  student_id: number
  username: string
  real_name: string
  class_id: number
  class_name: string
  status: string
  today_session_count: number
  today_item_count: number
  today_accuracy: number
  practice_count: number
  accuracy: number
  last_active_at: string | null
}

interface LiveBoardData {
  updated_at: string
  classes: LiveClass[]
  students: LiveStudent[]
}

// ----------------------------- 数据
const loading = ref(false)
const loadedOnce = ref(false)
const board = ref<LiveBoardData | null>(null)

// ----------------------------- 班级筛选
const classOptions = ref<{ label: string; value: number }[]>([])
const selectedClassId = ref<number | null>(null)

async function loadClassOptions(): Promise<void> {
  try {
    const res = await listClasses({ status: 'all', pageSize: 200 })
    classOptions.value = res.items.map((c: ClassItem) => ({ label: c.name, value: c.id }))
  } catch {
    // 错误由 client.ts 统一提示
  }
}

// ----------------------------- 轮询
const POLL_MS = 10_000
let timer: ReturnType<typeof setInterval> | null = null

async function load(): Promise<void> {
  loading.value = true
  try {
    const params: Record<string, unknown> = {}
    if (selectedClassId.value !== null) params.class_id = selectedClassId.value
    board.value = await api.get<LiveBoardData>('/stats/live-board', { params })
    loadedOnce.value = true
  } catch {
    // 错误由 client.ts 统一提示；保留上一次数据，不强行清空
  } finally {
    loading.value = false
  }
}

function startPolling(): void {
  stopPolling()
  timer = setInterval(() => {
    void load()
  }, POLL_MS)
}

function stopPolling(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

function onSelectClass(): void {
  void load()
}

onMounted(() => {
  void loadClassOptions()
  void load()
  startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<template>
  <div class="live-board">
    <PageHeader title="课堂实时大屏" :show-back="false">
      <template #extra>
        <span class="updated-at">
          更新于：{{ board ? formatDateTime(board.updated_at) : '—' }}
        </span>
        <el-select
          v-model="selectedClassId"
          placeholder="全部班级"
          clearable
          style="width: 160px; margin-left: 12px"
          @change="onSelectClass"
        >
          <el-option
            v-for="opt in classOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
        <el-button style="margin-left: 12px" :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <LoadingBox v-if="loading && !loadedOnce" />

    <EmptyState
      v-else-if="loadedOnce && board && board.classes.length === 0"
      title="暂无班级数据"
      description="先在「学生管理」中创建班级并导入学生，大屏才会显示内容。"
    />

    <template v-else-if="loadedOnce && board">
      <!-- 班级卡片 -->
      <div class="class-grid">
        <el-card v-for="c in board.classes" :key="c.class_id" shadow="hover" class="class-card">
          <template #header>
            <div class="class-card__head">
              <span class="class-card__name">{{ c.class_name }}</span>
              <el-tag size="small" :type="c.status === 'active' ? 'success' : 'info'">
                {{ c.status === 'active' ? '正常' : '已归档' }}
              </el-tag>
            </div>
          </template>
          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item label="在读人数">{{ c.student_count }}</el-descriptions-item>
            <el-descriptions-item label="今日活跃">{{ c.today_active_students }}</el-descriptions-item>
            <el-descriptions-item label="今日场次">{{ c.today_session_count }}</el-descriptions-item>
            <el-descriptions-item label="今日题量">{{ c.today_item_count }}</el-descriptions-item>
            <el-descriptions-item label="今日正确率" :span="2">
              {{ formatPercent(c.today_accuracy) }}
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </div>

      <!-- 学生明细 -->
      <el-card shadow="never" class="student-card">
        <template #header>学生实时练习</template>
        <el-table :data="board.students" stripe height="520" empty-text="该班级暂无学生">
          <el-table-column prop="real_name" label="姓名" min-width="100" />
          <el-table-column prop="username" label="学号" min-width="110" />
          <el-table-column prop="class_name" label="班级" min-width="110" />
          <el-table-column label="状态" min-width="80">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
                {{ row.status === 'active' ? '正常' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="today_session_count" label="今日场次" min-width="90" />
          <el-table-column prop="today_item_count" label="今日题量" min-width="90" />
          <el-table-column label="今日正确率" min-width="110">
            <template #default="{ row }">
              {{ formatPercent(row.today_accuracy) }}
            </template>
          </el-table-column>
          <el-table-column prop="practice_count" label="累计题量" min-width="90" />
          <el-table-column label="累计正确率" min-width="110">
            <template #default="{ row }">
              {{ formatPercent(row.accuracy) }}
            </template>
          </el-table-column>
          <el-table-column label="最近练习" min-width="150">
            <template #default="{ row }">
              {{ row.last_active_at ? formatDateTime(row.last_active_at) : '—' }}
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.live-board {
  padding: 0 4px;
}
.updated-at {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.class-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin: 16px 0;
}
.class-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.class-card__name {
  font-weight: 600;
  font-size: 15px;
}
.student-card {
  margin-bottom: 16px;
}
</style>
