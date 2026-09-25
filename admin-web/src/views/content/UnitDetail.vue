<script setup lang="ts">
/**
 * 单元详情 —— WP-WEB-02 F4
 *
 * 两个 Tab：词条 / 课文，均支持分页与分页大小切换（分别在 WordTable / TextTable 内实现）。
 * 后端无「单单元详情」接口，单元名通过词条列表的 meta 取得（subject 同步回显）。
 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import WordTable from './WordTable.vue'
import TextTable from './TextTable.vue'
import { listUnitWords, type Subject } from '@/api/content'

const route = useRoute()
const router = useRouter()
const unitId = Number(route.params.id)

const unitName = ref('')
const subject = ref<Subject | ''>('')

async function loadMeta() {
  try {
    const res = await listUnitWords(unitId, { page: 1, page_size: 1 })
    unitName.value = res.unit_name
    subject.value = res.subject
  } catch {
    // 失败不影响表格渲染，标题回退到 ID
  }
}

onMounted(loadMeta)
</script>

<template>
  <div class="admin-page">
    <PageHeader :title="unitName || `单元 #${unitId}`" :subtitle="subject ? (subject === 'english' ? '英语' : '语文') + ' · 单元详情' : '单元详情'">
      <el-button @click="router.push('/content/units')">返回列表</el-button>
    </PageHeader>

    <el-tabs type="border-card">
      <el-tab-pane label="词条">
        <WordTable :unit-id="unitId" />
      </el-tab-pane>
      <el-tab-pane label="课文">
        <TextTable :unit-id="unitId" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.admin-page :deep(.el-tabs) {
  background: var(--admin-panel);
}
</style>
