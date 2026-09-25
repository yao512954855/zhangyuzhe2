<script setup lang="ts">
/**
 * 词条编辑弹窗 —— WP-WEB-02 F7
 *
 * 仅负责收集表单并做前端校验，保存动作与「30003 / history_affected」二次确认
 * 交给父组件（WordTable）统一处理，避免把请求逻辑散落两处。
 */
import { computed, reactive, ref, watch } from 'vue'

import { type WordItem, type WordPatchPayload, type WordType } from '@/api/content'

const props = defineProps<{ visible: boolean; word: WordItem | null }>()
const emit = defineEmits<{
  'update:visible': [boolean]
  save: [{ id: number; data: WordPatchPayload }]
}>()

const formRef = ref()
const form = reactive<{
  type: WordType
  en: string
  cn: string
  acceptedText: string
  phonetic: string
  pos: string
  example_en: string
  example_cn: string
  require_spell: boolean
  order_no: number
  status: 'active' | 'inactive'
}>({
  type: 'word',
  en: '',
  cn: '',
  acceptedText: '',
  phonetic: '',
  pos: '',
  example_en: '',
  example_cn: '',
  require_spell: true,
  order_no: 0,
  status: 'active',
})

const rules = {
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  en: [{ required: true, message: '请输入英文', trigger: 'blur' }],
  cn: [{ required: true, message: '请输入中文', trigger: 'blur' }],
}

const visible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

watch(
  () => props.word,
  (w) => {
    if (!w) return
    form.type = w.type
    form.en = w.en
    form.cn = w.cn
    form.acceptedText = (w.accepted || []).join('\n')
    form.phonetic = w.phonetic ?? ''
    form.pos = w.pos ?? ''
    form.example_en = w.example_en ?? ''
    form.example_cn = w.example_cn ?? ''
    form.require_spell = w.require_spell
    form.order_no = w.order_no
    form.status = w.status
  },
  { immediate: true },
)

function parseAccepted(): string[] {
  return form.acceptedText
    .split(/[\n,，、]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function submit() {
  if (!formRef.value) return
  await formRef.value.validate()
  if (!props.word) return
  const data: WordPatchPayload = {
    type: form.type,
    en: form.en,
    cn: form.cn,
    accepted: parseAccepted(),
    phonetic: form.phonetic || null,
    pos: form.pos || null,
    example_en: form.example_en || null,
    example_cn: form.example_cn || null,
    require_spell: form.require_spell,
    order_no: form.order_no,
    status: form.status,
  }
  emit('save', { id: props.word.id, data })
}
</script>

<template>
  <el-dialog v-model="visible" title="编辑词条" width="520px" @closed="visible = false">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="88px">
      <el-form-item label="类型" prop="type">
        <el-select v-model="form.type" style="width: 100%">
          <el-option label="单词" value="word" />
          <el-option label="短语" value="phrase" />
          <el-option label="句子" value="sentence" />
        </el-select>
      </el-form-item>
      <el-form-item label="英文" prop="en">
        <el-input v-model="form.en" placeholder="如 classroom" />
      </el-form-item>
      <el-form-item label="中文" prop="cn">
        <el-input v-model="form.cn" placeholder="如 教室" />
      </el-form-item>
      <el-form-item label="可接受答案">
        <el-input v-model="form.acceptedText" type="textarea" :rows="2" placeholder="每行或逗号分隔，可留空" />
      </el-form-item>
      <el-form-item label="音标">
        <el-input v-model="form.phonetic" placeholder="如 /ˈklɑːsruːm/" />
      </el-form-item>
      <el-form-item label="词性">
        <el-input v-model="form.pos" placeholder="如 n." />
      </el-form-item>
      <el-form-item label="例句(英)">
        <el-input v-model="form.example_en" />
      </el-form-item>
      <el-form-item label="例句(中)">
        <el-input v-model="form.example_cn" />
      </el-form-item>
      <el-form-item label="是否拼读">
        <el-switch v-model="form.require_spell" />
      </el-form-item>
      <el-form-item label="序号">
        <el-input-number v-model="form.order_no" :min="0" :max="99999" />
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="form.status" style="width: 100%">
          <el-option label="启用" value="active" />
          <el-option label="停用" value="inactive" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>
