<script setup lang="ts">
/**
 * 单条新增词条弹窗 —— 英语内容管理页「＋添加单词」（2026-09-30）。
 *
 * 只收集表单与校验，保存动作（createWord + 成功刷新）交给父组件。
 * 单元下拉由父组件传入（已按当前年级/学期筛选），当前树上选中的单元作默认值。
 */
import { computed, reactive, ref, watch } from 'vue'

import { type UnitOptionItem, type WordType } from '@/api/content'

const props = defineProps<{
  visible: boolean
  units: UnitOptionItem[]
  defaultUnitId: number | null
}>()
const emit = defineEmits<{
  'update:visible': [boolean]
  save: [payload: {
    unit_id: number
    type: WordType
    en: string
    cn: string
    accepted: string[]
    phonetic: string | null
    pos: string | null
    example_en: string | null
    example_cn: string | null
    require_spell: boolean
  }]
}>()

const WORD_TYPE_OPTIONS: Array<{ label: string; value: WordType }> = [
  { label: '单词', value: 'word' },
  { label: '短语', value: 'phrase' },
  { label: '句子', value: 'sentence' },
]

const formRef = ref()
const form = reactive({
  unit_id: null as number | null,
  type: 'word' as WordType,
  en: '',
  cn: '',
  acceptedText: '',
  phonetic: '',
  pos: '',
  example_en: '',
  example_cn: '',
  require_spell: true,
})

const rules = {
  unit_id: [{ required: true, message: '请选择单元', trigger: 'change' }],
  en: [{ required: true, message: '请输入英文', trigger: 'blur' }],
  cn: [{ required: true, message: '请输入中文', trigger: 'blur' }],
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
    form.type = 'word'
    form.en = ''
    form.cn = ''
    form.acceptedText = ''
    form.phonetic = ''
    form.pos = ''
    form.example_en = ''
    form.example_cn = ''
    form.require_spell = true
  },
)

function submit() {
  void formRef.value.validate().then(() => {
    const accepted = form.acceptedText
      .split(/[,，、]/)
      .map((s) => s.trim())
      .filter(Boolean)
    emit('save', {
      unit_id: form.unit_id!,
      type: form.type,
      en: form.en.trim(),
      cn: form.cn.trim(),
      accepted,
      phonetic: form.phonetic.trim() || null,
      pos: form.pos.trim() || null,
      example_en: form.example_en.trim() || null,
      example_cn: form.example_cn.trim() || null,
      require_spell: form.require_spell,
    })
  })
}
</script>

<template>
  <el-dialog v-model="visible" title="添加单词" width="520px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
      <el-form-item label="单元" prop="unit_id">
        <el-select v-model="form.unit_id" placeholder="选择单元" style="width: 100%">
          <el-option v-for="u in units" :key="u.id" :label="`${u.name}（${u.grade}年级 · ${u.semester === 'first' ? '上学期' : '下学期'}）`" :value="u.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="类型">
        <el-radio-group v-model="form.type">
          <el-radio v-for="o in WORD_TYPE_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="英文" prop="en">
        <el-input v-model="form.en" placeholder="如 classroom" />
      </el-form-item>
      <el-form-item label="中文" prop="cn">
        <el-input v-model="form.cn" placeholder="如 教室" />
      </el-form-item>
      <el-form-item label="可接受答案">
        <el-input v-model="form.acceptedText" placeholder="可选，多条用逗号分隔，如 喂,哈罗" />
      </el-form-item>
      <el-form-item label="音标">
        <el-input v-model="form.phonetic" placeholder="可选，如 /ˈklɑːsruːm/" />
      </el-form-item>
      <el-form-item label="词性">
        <el-input v-model="form.pos" placeholder="可选，如 n. / v." />
      </el-form-item>
      <el-form-item label="例句（英）">
        <el-input v-model="form.example_en" placeholder="可选" />
      </el-form-item>
      <el-form-item label="例句（中）">
        <el-input v-model="form.example_cn" placeholder="可选" />
      </el-form-item>
      <el-form-item label="是否拼读">
        <el-radio-group v-model="form.require_spell">
          <el-radio :value="true">是</el-radio>
          <el-radio :value="false">否</el-radio>
        </el-radio-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>
