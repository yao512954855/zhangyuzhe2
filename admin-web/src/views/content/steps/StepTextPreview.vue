<script setup lang="ts">
/**
 * 导入向导 · 步骤 3（课文预览）—— WP-WEB-02 F13
 *
 * 段落列表渲染，每段一个复选框（默认按模板勾选），用户可调整背诵范围；
 * 显示每段字数与总字数；提交时回传**调整后的** `recite_scope`（按 Excel 行号映射）。
 *
 * 关键交互：背诵范围必须让用户勾选，不能靠用户在 Excel 里算段落号（需求 §4.5）。
 */
import { reactive, ref } from 'vue'

import { type TextCommitScope, type TextsPreview, type PreviewText } from '@/api/imports'

const props = defineProps<{ data: TextsPreview }>()
const emit = defineEmits<{
  commit: [TextCommitScope[]]
  back: []
}>()

const textPage = ref(1)
const textPageSize = 10
const pagedTexts = () =>
  props.data.texts.slice((textPage.value - 1) * textPageSize, textPage.value * textPageSize)

/** 每个 text（按 row）勾选的段落下标集合。默认按后端给的 recite_scope。 */
const checked: Record<number, Set<number>> = reactive({})
for (const t of props.data.texts) {
  checked[t.row] = new Set(t.recite_scope)
}

function toggle(t: PreviewText, idx: number, val: boolean) {
  const set = checked[t.row]
  if (val) set.add(idx)
  else set.delete(idx)
}

function selectAll(t: PreviewText, val: boolean) {
  const set = checked[t.row]
  set.clear()
  if (val) t.paragraphs.forEach((p) => set.add(p.idx))
}

const totalCharsOf = (t: PreviewText) =>
  t.paragraphs.filter((p) => checked[t.row].has(p.idx)).reduce((s, p) => s + p.chars, 0)

function confirmCommit() {
  const scopes: TextCommitScope[] = props.data.texts.map((t) => ({
    row: t.row,
    recite_scope: Array.from(checked[t.row]).sort((a, b) => a - b),
  }))
  emit('commit', scopes)
}
</script>

<template>
  <div class="text-preview">
    <el-alert
      v-if="data.pinyin_issues.length > 0"
      type="error"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      有 {{ data.pinyin_issues.length }} 篇课文存在无法拼音化的字（语文对齐会漏字），入库后将无法正确判定背诵。
      请下载错误报告，修正正文后重新导入。
    </el-alert>

    <el-alert
      v-if="data.invalid_rows.length > 0"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    >
      另有 {{ data.invalid_rows.length }} 行（标题/学科等字段）校验失败，不会入库。
    </el-alert>

    <p class="hint">请勾选每篇课文需要背诵的段落（默认按模板全选）。提交时按你勾选的范围入库。</p>

    <div v-for="t in pagedTexts()" :key="t.row" class="text-card">
      <div class="text-card__head">
        <div>
          <strong>{{ t.title }}</strong>
          <span class="muted">（第 {{ t.row }} 行 · {{ t.author || '佚名' }} · 全文 {{ t.total_chars }} 字）</span>
        </div>
        <div class="text-card__ops">
          <el-button link size="small" @click="selectAll(t, true)">全选</el-button>
          <el-button link size="small" @click="selectAll(t, false)">全不选</el-button>
          <span class="selected-chars">已选背诵 {{ totalCharsOf(t) }} 字</span>
        </div>
      </div>
      <div class="paragraphs">
        <label v-for="p in t.paragraphs" :key="p.idx" class="paragraph">
          <el-checkbox
            :model-value="checked[t.row].has(p.idx)"
            @update:model-value="(v: boolean) => toggle(t, p.idx, v)"
          />
          <span class="paragraph__idx">第 {{ p.idx + 1 }} 段</span>
          <span class="paragraph__text">{{ p.text }}</span>
          <span class="paragraph__chars">{{ p.chars }} 字</span>
        </label>
      </div>
    </div>

    <el-pagination
      v-if="data.texts.length > textPageSize"
      v-model:current-page="textPage"
      :page-size="textPageSize"
      :total="data.texts.length"
      layout="prev, pager, next"
      style="margin: 12px 0; justify-content: flex-end"
    />

    <div class="actions">
      <el-button @click="emit('back')">上一步</el-button>
      <el-button type="primary" @click="confirmCommit">确认入库</el-button>
    </div>
  </div>
</template>

<style scoped>
.hint {
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.text-card {
  border: 1px solid var(--admin-border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.text-card__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.muted {
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.text-card__ops {
  display: flex;
  align-items: center;
  gap: 8px;
}
.selected-chars {
  color: var(--admin-brand);
  font-size: 13px;
}
.paragraphs {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.paragraph {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--admin-bg);
}
.paragraph__idx {
  flex: 0 0 56px;
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.paragraph__text {
  flex: 1;
  line-height: 1.5;
}
.paragraph__chars {
  flex: 0 0 56px;
  text-align: right;
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
</style>
