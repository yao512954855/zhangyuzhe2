<script setup lang="ts">
/**
 * 内容版本快照 —— WP-2I（二期工作包 9）
 *
 * 管理端查看某条内容（unit / word / text）的版本历史，并支持回滚到指定快照。
 * 所有请求走 `client.ts` 的 `api`（统一解包 / 统一错误提示），页面层不再重复弹错。
 *
 * ⚠️ 回滚**不会删除**任何内容（后端保证），故带练习记录的内容也能安全回滚到旧状态。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import EmptyState from '@/components/EmptyState.vue'
import { api, BizError } from '@/api/client'

type EntityType = 'unit' | 'word' | 'text'

interface VersionItem {
  id: number
  entity_type: EntityType
  entity_id: number
  version: number
  op: 'create' | 'update' | 'disable' | 'delete'
  operator_id: number | null
  created_at: string | null
}

const OP_LABEL: Record<VersionItem['op'], string> = {
  create: '新增',
  update: '修改',
  disable: '停用',
  delete: '删除',
}

const query = reactive<{ entity_type: EntityType | ''; entity_id: number | null }>({
  entity_type: 'unit',
  entity_id: null,
})

const loading = ref(false)
const loadedOnce = ref(false)
const versions = ref<VersionItem[]>([])

async function load() {
  if (!query.entity_type || query.entity_id === null || query.entity_id === undefined) {
    ElMessage.warning('请选择实体类型并填写实体 ID')
    return
  }
  loading.value = true
  try {
    const res = await api.get<{ items: VersionItem[] }>('/content/versions', {
      params: { entity_type: query.entity_type, entity_id: query.entity_id },
    })
    versions.value = res.items
    loadedOnce.value = true
  } catch {
    // client 已统一弹提示
  } finally {
    loading.value = false
  }
}

async function rollback(row: VersionItem) {
  try {
    await ElMessageBox.confirm(
      `确定回滚到版本 ${row.version}（${OP_LABEL[row.op]}）吗？回滚会把该内容恢复到此版本的状态，并再记一条版本。`,
      '确认回滚',
      { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await api.post(`/content/versions/${row.id}/rollback`)
    ElMessage.success(`已回滚到版本 ${row.version}`)
    await load()
  } catch (e) {
    // 70005（版本不存在）等由 client 统一提示；其它错误也已统一处理
    if (e instanceof BizError && e.code === 70005) {
      await load()
    }
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="内容版本快照" subtitle="查看单元 / 词条 / 课文的变更历史，并可回滚到指定版本">
      <el-button @click="load">查询</el-button>
    </PageHeader>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="实体类型">
          <el-select v-model="query.entity_type" placeholder="请选择" style="width: 140px">
            <el-option label="单元" value="unit" />
            <el-option label="词条" value="word" />
            <el-option label="课文" value="text" />
          </el-select>
        </el-form-item>
        <el-form-item label="实体 ID">
          <el-input-number v-model="query.entity_id" :min="1" :controls="false" placeholder="如 1" style="width: 160px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="load">查询</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6" class="table-wrap">
      <EmptyState v-if="loadedOnce && versions.length === 0" description="该内容暂无版本记录" />

      <el-table v-else :data="versions" border stripe>
        <el-table-column prop="id" label="版本 ID" width="100" />
        <el-table-column prop="version" label="版本号" width="90" align="center" />
        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.op === 'delete' ? 'danger' : row.op === 'disable' ? 'warning' : 'info'">
              {{ OP_LABEL[row.op as VersionItem['op']] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator_id" label="操作人 ID" width="120" align="center" />
        <el-table-column prop="created_at" label="记录时间" min-width="200" />
        <el-table-column label="操作" min-width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="rollback(row)">回滚到此版本</el-button>
          </template>
        </el-table-column>
      </el-table>
    </LoadingBox>
  </div>
</template>

<style scoped>
.filter-card {
  margin-bottom: 16px;
}
.table-wrap {
  background: var(--admin-panel);
}
</style>
