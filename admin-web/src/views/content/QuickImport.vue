<script setup lang="ts">
/**
 * 快速导入 —— todo13 #4（2026-10-19 甲方反馈）：竖线粘贴导入，Excel 导入的补充。
 *
 * - 左侧 UnitTree 选目标分类（必须是末级分类），类型下拉（默认单词）；
 * - 文本域一行一条，竖线「|」分隔：2 段=英文|中文，3 段=英文|词性|中文
 *   （短句类型时中间段并入中文）；空行跳过；1 段 / >3 段该行报错带行号；
 * - 结果弹窗与 Excel 导入同构（inserted / duplicated / failed 行号+原因）。
 */
import { computed, onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import UnitTree from '@/components/UnitTree.vue'
import {
  ITEM_TYPES,
  ITEM_TYPE_LABEL,
  importTextItems,
} from '@/api/items'
import { isLeafUnit } from '@/api/types'
import type { ItemImportResult, ItemType, UnitNode } from '@/api/types'

const treeRef = ref<InstanceType<typeof UnitTree> | null>(null)
const selectedUnit = ref<UnitNode | null>(null)
const activeType = ref<ItemType>('word')
const textInput = ref('')
const busy = ref(false)

const subtitle = computed(() => {
  if (!selectedUnit.value) return '先在左侧选择目标分类'
  const leafHint = isLeafUnit(selectedUnit.value) ? '' : '（有下级分类，不能导入）'
  return `${selectedUnit.value.name} · ${ITEM_TYPE_LABEL[activeType.value]}${leafHint}`
})

/** 占位示例（选中分类后展示在文本域上方）。 */
const placeholder =
  'face | n. | 脸，面部，脸色\ngood morning | 早上好\nHow are you | 你好'

function handleSelectUnit(unit: UnitNode): void {
  selectedUnit.value = unit
}

const resultVisible = ref(false)
const result = ref<ItemImportResult | null>(null)

async function doImport(): Promise<void> {
  if (!selectedUnit.value) {
    ElMessage.warning('请先在左侧选择目标分类')
    return
  }
  if (!isLeafUnit(selectedUnit.value)) {
    ElMessage.warning('该分类下有下级分类，请选择末级分类后再导入')
    return
  }
  const text = textInput.value
  if (!text.trim()) {
    ElMessage.warning('请先粘贴内容（一行一条，竖线分隔）')
    return
  }

  busy.value = true
  try {
    result.value = await importTextItems(text, selectedUnit.value.id, activeType.value)
    resultVisible.value = true
    treeRef.value?.refresh() // counts 角标同步
  } catch {
    // 统一提示（client.ts 已弹）
  } finally {
    busy.value = false
  }
}

function clearAll(): void {
  textInput.value = ''
  result.value = null
}

onActivated(() => {
  treeRef.value?.refresh()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader title="快速导入" :subtitle="subtitle">
      <el-button @click="clearAll" :disabled="busy">清空</el-button>
      <el-button type="primary" :loading="busy" @click="doImport">导入</el-button>
    </PageHeader>

    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="never">
          <template #header>
            <div class="qi-card__header"><span>目标分类</span></div>
          </template>
          <UnitTree ref="treeRef" @select="handleSelectUnit" />
        </el-card>
      </el-col>

      <el-col :span="18">
        <el-card shadow="never">
          <template #header>
            <div class="qi-card__header">
              <span>粘贴内容</span>
              <el-radio-group v-model="activeType" size="small">
                <el-radio-button v-for="t in ITEM_TYPES" :key="t" :value="t">
                  {{ ITEM_TYPE_LABEL[t] }}
                </el-radio-button>
              </el-radio-group>
            </div>
          </template>

          <el-alert type="info" :closable="false" show-icon class="qi-alert">
            <p>一行一条，用竖线「|」分隔；两段 = 英文|中文，三段 = 英文|词性|中文</p>
            <p>选「短句」时三段中的词性段会并入中文；空行跳过；格式错误的行会报出行号</p>
          </el-alert>

          <el-input
            v-model="textInput"
            type="textarea"
            :rows="14"
            :placeholder="placeholder"
            class="qi-textarea"
          />
        </el-card>
      </el-col>
    </el-row>

    <!-- 导入结果弹窗（与 Excel 导入同构） -->
    <el-dialog v-model="resultVisible" title="导入结果" width="520px">
      <template v-if="result">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="新增">{{ result.inserted }}</el-descriptions-item>
          <el-descriptions-item label="重复跳过">{{ result.duplicated }}</el-descriptions-item>
          <el-descriptions-item label="失败">{{ result.failed.length }}</el-descriptions-item>
        </el-descriptions>

        <template v-if="result.failed.length > 0">
          <div class="qi-failed-title">失败明细（行号 + 原因）</div>
          <el-table :data="result.failed" border size="small" max-height="260">
            <el-table-column prop="row" label="行号" width="80" align="center" />
            <el-table-column prop="reason" label="原因" min-width="240" />
          </el-table>
        </template>
      </template>
      <template #footer>
        <el-button type="primary" @click="resultVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.qi-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}

.qi-alert {
  margin-bottom: 12px;
}

.qi-alert p {
  margin: 2px 0;
  font-size: 13px;
  line-height: 1.6;
}

.qi-textarea {
  font-family: Consolas, Menlo, monospace;
}

.qi-failed-title {
  margin: 12px 0 8px;
  font-size: 13px;
  font-weight: 600;
}
</style>
