<script setup lang="ts">
/**
 * 单元树（两页共用）—— WP-B。
 *
 * 数据来自 `GET /api/units`（全量、不分页）。简化版没有层级，就是一份按
 * `sort, id` 排好的平铺列表，所以用 `el-tree` 平铺渲染 + 右侧内容数量角标。
 *
 * 用法：
 *   <UnitTree ref="treeRef" v-model="selectedId" @select="onSelect" />
 *   treeRef.value?.refresh()   // 增删改单元后刷新（保持当前选中）
 */
import { computed, onMounted, ref } from 'vue'

import { listUnits } from '@/api/units'
import type { UnitNode } from '@/api/types'

const props = defineProps<{
  /** 当前选中单元 id（v-model）；null = 未选 */
  modelValue?: number | null
}>()

const emit = defineEmits<{
  /** 选中变化（点节点）。 */
  (e: 'update:modelValue', id: number | null): void
  /** 选中单元对象（含 counts，父页面可直接用）。 */
  (e: 'select', unit: UnitNode): void
}>()

const loading = ref(false)
const units = ref<UnitNode[]>([])
const loadedOnce = ref(false)

/** el-tree 数据：直接用 UnitNode（props 里声明 label/id 取法）。 */
const treeData = computed(() => units.value)

/** 选中 id → 单元对象（父页面刷新 counts 后仍可拿到最新对象）。 */
const selectedUnit = computed(() => units.value.find((u) => u.id === props.modelValue) ?? null)

async function load(): Promise<void> {
  loading.value = true
  try {
    units.value = await listUnits()
    loadedOnce.value = true
    // 选中项可能已被（别的页面）删除，收敛掉
    if (props.modelValue != null && !selectedUnit.value) {
      emit('update:modelValue', null)
    }
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

function handleNodeClick(unit: UnitNode): void {
  emit('update:modelValue', unit.id)
  emit('select', unit)
}

/** 单元总内容数（三类合计，树节点右侧角标）。 */
function totalCount(unit: UnitNode): number {
  return unit.counts.word + unit.counts.phrase + unit.counts.sentence
}

defineExpose({
  /** 重新拉取单元列表。 */
  refresh: load,
  /** 当前选中的单元对象。 */
  selectedUnit,
})

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="unit-tree">
    <el-tree
      v-if="loadedOnce && units.length > 0"
      :data="treeData"
      node-key="id"
      :props="{ label: 'name', children: 'children' }"
      :current-node-key="modelValue ?? undefined"
      :expand-on-click-node="false"
      highlight-current
      class="unit-tree__body"
      @node-click="handleNodeClick"
    >
      <template #default="{ data }">
        <div class="unit-tree__node">
          <span class="unit-tree__name">{{ data.name }}</span>
          <span class="unit-tree__count">{{ totalCount(data) }}</span>
        </div>
      </template>
    </el-tree>

    <el-empty
      v-else-if="loadedOnce"
      description="还没有单元，请到「分类」页新建"
      :image-size="72"
    />
  </div>
</template>

<style scoped>
.unit-tree {
  min-height: 200px;
}

.unit-tree__body {
  background: transparent;
}

.unit-tree__body :deep(.el-tree-node__content) {
  height: 36px;
}

.unit-tree__node {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex: 1;
  min-width: 0;
  padding-right: 8px;
}

.unit-tree__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.unit-tree__count {
  flex: 0 0 auto;
  min-width: 20px;
  padding: 0 6px;
  border-radius: 10px;
  background: var(--el-fill-color);
  color: var(--admin-text-secondary);
  font-size: 12px;
  text-align: center;
  line-height: 18px;
}
</style>
