<script setup lang="ts">
/**
 * 单条新增课文弹窗 —— 语文内容管理页「＋添加课文」（2026-09-30）。
 *
 * 正文按空行/换行分段（与导入同一套服务端解析）；
 * 背诵范围可选：段落号从 0 数起、逗号分隔，留空 = 全文背诵。
 * 拼音校验在服务端（40003 会由 client 统一弹提示）。
 */
import { computed, reactive, ref, watch } from 'vue'

import { ElMessage } from 'element-plus'

import { type UnitOptionItem, createText, type TextDetail } from '@/api/content'

const props = defineProps<{
  visible: boolean
  units: UnitOptionItem[]
  defaultUnitId: number | null
}>()
const emit = defineEmits<{
  'update:visible': [boolean]
  saved: [text: TextDetail]
}>()

const formRef = ref()
const saving = ref(false)
const form = reactive({
  unit_id: null as number | null,
  title: '',
  author: '',
  contentRaw: '',
  scopeText: '',
})

const rules = {
  unit_id: [{ required: true, message: '请选择单元', trigger: 'change' }],
  title: [{ required: true, message: '请输入课文标题', trigger: 'blur' }],
  contentRaw: [{ required: true, message: '请输入段落正文', trigger: 'blur' }],
}

const visible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

watch(
  () => props.visible,
  (v) => {
    if (!v) return
    form.unit_id = props.defaultUnitId ?? props.units[0]?.id ?? null
    form.title = ''
    form.author = ''
    form.contentRaw = ''
    form.scopeText = ''
  },
)

async function submit() {
  await formRef.value.validate()
  let scope: number[] | null = null
  const text = form.scopeText.trim()
  if (text) {
    scope = text
      .split(/[,，、\s]+/)
      .map((s) => Number(s))
      .filter((n) => Number.isInteger(n) && n >= 0)
    if (scope.length === 0) {
      ElMessage.warning('背诵范围格式不对：请填段落号（从 0 数起），如 0,2；留空表示全文背诵')
      return
    }
  }
  saving.value = true
  try {
    const detail = await createText({
      unit_id: form.unit_id!,
      title: form.title.trim(),
      author: form.author.trim() || null,
      content_raw: form.contentRaw,
      recite_scope: scope,
    })
    ElMessage.success('课文已添加')
    visible.value = false
    emit('saved', detail)
  } catch {
    // 40003 拼音失配等由 client 统一提示
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="添加课文" width="560px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
      <el-form-item label="单元" prop="unit_id">
        <el-select v-model="form.unit_id" placeholder="选择单元" style="width: 100%">
          <el-option v-for="u in units" :key="u.id" :label="`${u.name}（${u.grade}年级 · ${u.semester === 'first' ? '上学期' : '下学期'}）`" :value="u.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="标题" prop="title">
        <el-input v-model="form.title" style="width: 320px" placeholder="如 春" />
      </el-form-item>
      <el-form-item label="作者">
        <el-input v-model="form.author" style="width: 320px" placeholder="可选，如 朱自清" />
      </el-form-item>
      <el-form-item label="段落正文" prop="contentRaw">
        <el-input
          v-model="form.contentRaw"
          type="textarea"
          :rows="8"
          placeholder="每段一行（或用空行分隔）。保存后系统会自动做拼音校验，含无法拼音化的字会提示"
        />
      </el-form-item>
      <el-form-item label="背诵范围">
        <el-input v-model="form.scopeText" placeholder="可选，段落号从 0 数起，如 0,2；留空 = 全文背诵" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>
