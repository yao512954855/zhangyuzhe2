<script setup lang="ts">
/**
 * 课文查看 / 编辑弹窗 —— 语文内容管理页行内「查看」「编辑」（2026-09-30）。
 *
 * - 查看模式：段落逐段展示，背诵段正文色 + 「背」标记，不背段灰显 + 「不背」标记（带图例）
 * - 编辑模式：标题 / 作者 / 正文（空行分段）/ 背诵范围（段落号从 0 数起，留空 = 全文）
 *   提交走 `PATCH /api/texts/{id}`，正文改动会触发服务端重算拼音序列（40003 由 client 统一提示）；
 *   该课文已有背诵记录时后端 30003 拒改正文 —— 编辑前预检并提示，避免用户填完才被打回。
 */
import { computed, reactive, ref, watch } from 'vue'

import { ElMessage } from 'element-plus'

import { getText, patchText, type TextDetail } from '@/api/content'

const props = defineProps<{
  visible: boolean
  mode: 'view' | 'edit'
  textId: number | null
  /** 列表行自带的标题/作者（编辑预检用，避免多打一次接口） */
  fallbackTitle?: string
  fallbackAuthor?: string | null
}>()
const emit = defineEmits<{
  'update:visible': [boolean]
  saved: [text: TextDetail]
}>()

const loading = ref(false)
const saving = ref(false)
const detail = ref<TextDetail | null>(null)

const formRef = ref()
const form = reactive({
  title: '',
  author: '',
  contentRaw: '',
  scopeText: '',
})

const rules = {
  title: [{ required: true, message: '请输入课文标题', trigger: 'blur' }],
  contentRaw: [{ required: true, message: '请输入段落正文', trigger: 'blur' }],
}

const visible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

/** 与服务端 split_paragraphs 同口径的段落数估算（按换行切、忽略空行），用于范围预检 */
const estParagraphCount = computed(() =>
  form.contentRaw.split(/\n+/).map((s) => s.trim()).filter(Boolean).length,
)

watch(
  () => props.visible,
  async (v) => {
    if (!v || props.textId === null) return
    detail.value = null
    loading.value = true
    try {
      const d = await getText(props.textId)
      detail.value = d
      if (props.mode === 'edit') {
        form.title = d.title
        form.author = d.author ?? ''
        form.contentRaw = d.content_raw ?? ''
        form.scopeText = d.recite_scope.join(',')
      }
    } catch {
      visible.value = false
    } finally {
      loading.value = false
    }
  },
)

function scopeParse(text: string): { ok: boolean; scope: number[] | null } {
  const t = text.trim()
  if (!t) return { ok: true, scope: null }
  const arr = t
    .split(/[,，、\s]+/)
    .map((s) => Number(s))
    .filter((n) => Number.isInteger(n) && n >= 0)
  if (arr.length === 0) return { ok: false, scope: null }
  return { ok: true, scope: arr }
}

async function submit() {
  await formRef.value.validate()
  const parsed = scopeParse(form.scopeText)
  if (!parsed.ok) {
    ElMessage.warning('背诵范围格式不对：请填段落号（从 0 数起），如 0,2；留空表示全文背诵')
    return
  }
  const maxIdx = estParagraphCount.value - 1
  if (parsed.scope !== null && parsed.scope.some((i) => i > maxIdx)) {
    ElMessage.warning(`背诵范围里的段落号超出实际段落数（当前约 ${estParagraphCount.value} 段，从 0 数起）`)
    return
  }
  saving.value = true
  try {
    const payload: Parameters<typeof patchText>[1] = {
      title: form.title.trim(),
      author: form.author.trim() || null,
      content_raw: form.contentRaw,
      recite_scope: parsed.scope,
    }
    const d = await patchText(props.textId!, payload)
    ElMessage.success('课文已保存')
    visible.value = false
    emit('saved', d)
  } catch {
    // 30003（有记录拒改正文）/ 40003（拼音失配）等由 client 统一提示
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="mode === 'view' ? `课文预览${detail ? ` · ${detail.title}` : ''}` : `编辑课文${fallbackTitle ? ` · ${fallbackTitle}` : ''}`"
    width="640px"
    :close-on-click-modal="mode === 'view'"
  >
    <div v-loading="loading" class="text-dialog__body">
      <!-- ================= 查看 ================= -->
      <template v-if="mode === 'view'">
        <template v-if="detail">
          <div class="text-view__meta">
            <span v-if="detail.author" class="text-view__author">作者：{{ detail.author }}</span>
            <span>共 {{ detail.paragraphs.length }} 段 · 全文 {{ detail.total_chars }} 字 · 背诵 {{ detail.recite_scope.length }} 段</span>
          </div>
          <div class="text-view__legend">
            <span class="lg lg--recite">背</span><span>需背诵段落</span>
            <span class="lg lg--skip">不背</span><span>不进入背诵判分</span>
          </div>
          <ol class="text-view__paras">
            <li
              v-for="p in detail.paragraphs"
              :key="p.idx"
              :class="p.recite ? 'text-view__para--recite' : 'text-view__para--skip'"
            >
              <span class="text-view__para-idx">{{ p.idx }}</span>
              <span class="text-view__para-text">{{ p.text }}</span>
              <span class="text-view__para-tag">{{ p.recite ? '背' : '不背' }}</span>
            </li>
          </ol>
        </template>
      </template>

      <!-- ================= 编辑 ================= -->
      <template v-else>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="改正文会重算拼音序列；若该课文已有学生背诵记录，服务端会拒绝改正文（历史判分口径保护），届时请停用后另建新课文。标题 / 作者随时可改。"
          class="text-edit__hint"
        />
        <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
          <el-form-item label="标题" prop="title">
            <el-input v-model="form.title" style="width: 320px" />
          </el-form-item>
          <el-form-item label="作者">
            <el-input v-model="form.author" style="width: 320px" placeholder="可选" />
          </el-form-item>
          <el-form-item label="段落正文" prop="contentRaw">
            <el-input
              v-model="form.contentRaw"
              type="textarea"
              :rows="8"
              placeholder="每段一行（或用空行分隔）"
            />
            <div class="text-edit__count">当前约 {{ estParagraphCount }} 段</div>
          </el-form-item>
          <el-form-item label="背诵范围">
            <el-input v-model="form.scopeText" placeholder="段落号从 0 数起，如 0,2；留空 = 全文背诵" />
          </el-form-item>
        </el-form>
      </template>
    </div>
    <template #footer>
      <el-button @click="visible = false">{{ mode === 'view' ? '关闭' : '取消' }}</el-button>
      <el-button v-if="mode === 'edit'" type="primary" :loading="saving" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.text-dialog__body {
  min-height: 120px;
}
.text-view__meta {
  display: flex;
  gap: 16px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}
.text-view__author {
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.text-view__legend {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 10px;
}
.lg {
  display: inline-block;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 12px;
  line-height: 18px;
}
.lg--recite {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.lg--skip {
  background: var(--el-fill-color);
  color: var(--el-text-color-placeholder);
  text-decoration: line-through;
}
.text-view__paras {
  margin: 0;
  padding: 0;
  list-style: none;
}
.text-view__paras li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  margin-bottom: 6px;
  line-height: 1.8;
}
.text-view__para--recite {
  background: var(--el-color-primary-light-9);
}
.text-view__para--skip {
  background: var(--el-fill-color-light);
}
.text-view__para--skip .text-view__para-text {
  color: var(--el-text-color-placeholder);
  text-decoration: line-through;
}
.text-view__para-idx {
  flex: none;
  width: 22px;
  text-align: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color);
  border-radius: 3px;
  margin-top: 4px;
}
.text-view__para-tag {
  flex: none;
  font-size: 12px;
  padding: 0 6px;
  border-radius: 3px;
  margin-top: 4px;
}
.text-view__para--recite .text-view__para-tag {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-8);
}
.text-view__para--skip .text-view__para-tag {
  color: var(--el-text-color-placeholder);
  background: var(--el-fill-color);
}
.text-edit__hint {
  margin-bottom: 14px;
}
.text-edit__count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
</style>
