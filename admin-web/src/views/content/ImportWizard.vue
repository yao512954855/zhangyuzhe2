<script setup lang="ts">
/**
 * 导入向导主容器 —— WP-WEB-02 F10/F13(F16)/F15
 *
 * 四步：① 选类型+模板 ② 上传校验 ③ 预览修正 ④ 确认入库看结果；步骤条常驻可见。
 * 关键交互：向导状态用 sessionStorage 持久化（preview_token + 当前步骤 + 预览数据），
 * 刷新后无需重传文件即可恢复到预览/结果步（前提：preview_token 30 分钟有效期内）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import StepUpload from './steps/StepUpload.vue'
import StepWordPreview from './steps/StepWordPreview.vue'
import StepTextPreview from './steps/StepTextPreview.vue'
import StepResult from './steps/StepResult.vue'
import {
  commitTexts,
  commitWords,
  type DuplicateStrategy,
  type ImportType,
  type TextsCommitResult,
  type TextsPreview,
  type WordsCommitResult,
  type WordsPreview,
} from '@/api/imports'

const STORAGE_KEY = 'bjsys_import_wizard'

const currentStep = ref(1)
const type = ref<ImportType | ''>('')
const previewData = ref<WordsPreview | TextsPreview | null>(null)
const result = ref<WordsCommitResult | TextsCommitResult | null>(null)
const committing = ref(false)

const persist = reactive<{
  step: number
  type: ImportType | ''
  preview_token: string
  previewData: WordsPreview | TextsPreview | null
}>({ step: 1, type: '', preview_token: '', previewData: null })

function savePersist() {
  persist.step = currentStep.value
  persist.type = type.value
  persist.previewData = previewData.value
  persist.preview_token = previewData.value?.preview_token ?? ''
  // 仅在 preview 之后（有 token）有意义地持久化；否则清掉
  if (currentStep.value >= 3 && previewData.value) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(persist))
    } catch {
      // sessionStorage 可能写不下超大预览数据，忽略即可（刷新后回到第 1 步）
    }
  }
}

function clearPersist() {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

function restore() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const saved = JSON.parse(raw) as typeof persist
    // 只有已到预览/结果步、且 token 仍在，才恢复（避免刷新后跳到过期页面）
    if (saved.step >= 3 && saved.previewData && saved.preview_token) {
      type.value = saved.type
      previewData.value = saved.previewData
      currentStep.value = saved.step
    }
  } catch {
    clearPersist()
  }
}

// 状态变化时持久化
watch([currentStep, type, previewData], savePersist, { deep: true })

onMounted(restore)

// ----------------------------- 事件处理
function onTypeChosen(t: ImportType) {
  type.value = t
  currentStep.value = 2
}

function onPreviewed(payload: { type: ImportType; data: WordsPreview | TextsPreview }) {
  type.value = payload.type
  previewData.value = payload.data
  currentStep.value = 3
}

async function onWordCommit(opts: { createMissingUnits: boolean; onDuplicate: DuplicateStrategy }) {
  if (!previewData.value) return
  committing.value = true
  try {
    const res = await commitWords(previewData.value.preview_token, opts.createMissingUnits, opts.onDuplicate)
    result.value = res
    currentStep.value = 4
    clearPersist() // token 一次性，提交后失效
  } catch {
    // 业务错误已由 client 统一提示
  } finally {
    committing.value = false
  }
}

async function onTextCommit(scopes: { row: number; recite_scope: number[] }[]) {
  if (!previewData.value) return
  committing.value = true
  try {
    const res = await commitTexts(previewData.value.preview_token, scopes)
    result.value = res
    currentStep.value = 4
    clearPersist()
  } catch {
    // 统一提示
  } finally {
    committing.value = false
  }
}

function backToUpload() {
  previewData.value = null
  currentStep.value = 2
  clearPersist()
}

function resetWizard() {
  currentStep.value = 1
  type.value = ''
  previewData.value = null
  result.value = null
  clearPersist()
}

const steps = [
  { title: '选择类型' },
  { title: '上传校验' },
  { title: '预览修正' },
  { title: '确认入库' },
]

const isWords = computed(() => type.value === 'words')
</script>

<template>
  <div class="admin-page">
    <PageHeader title="导入向导" subtitle="用 Excel 模板批量导入英语词条 / 语文课文">
      <el-button @click="resetWizard">重新开始</el-button>
    </PageHeader>

    <el-steps :active="currentStep" align-center class="wizard-steps">
      <el-step v-for="(s, i) in steps" :key="i" :title="s.title" />
    </el-steps>

    <el-card shadow="never" class="wizard-body">
      <StepUpload
        v-if="currentStep <= 2"
        :type="type"
        @update:type="onTypeChosen"
        @previewed="onPreviewed"
      />

      <StepWordPreview
        v-else-if="currentStep === 3 && isWords && previewData"
        :data="previewData as WordsPreview"
        @commit="onWordCommit"
        @back="backToUpload"
      />

      <StepTextPreview
        v-else-if="currentStep === 3 && !isWords && previewData"
        :data="previewData as TextsPreview"
        @commit="onTextCommit"
        @back="backToUpload"
      />

      <div v-else-if="currentStep === 4 && result" class="result-wrap">
        <el-button v-if="committing" :loading="true">入库中…</el-button>
        <StepResult :type="type as ImportType" :result="result" @continue="resetWizard" />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.wizard-steps {
  margin-bottom: 20px;
}
.wizard-body {
  min-height: 320px;
}
.result-wrap {
  text-align: center;
}
</style>
