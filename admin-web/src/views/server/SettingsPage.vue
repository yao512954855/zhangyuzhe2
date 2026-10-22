<script setup lang="ts">
/**
 * 练习设置 —— WEB-11 F14-4（todo11 A3/A5，决策 #10/#13）。
 *
 * - 听写倒计时 dictation_countdown_s（1~10 秒数字输入）；
 * - 背诵判定倒计时 spell_verdict_countdown_s（1~10）；
 * - 报词显示语言 dictation_hint（zh / en 单选）；
 * - 云端语音 API Key cloud_api_key（password 输入，保存即生效无需重启；
 *   GET 返回掩码，输入留空 = 不修改，另有「清除」= 传空串回落 asr.yaml 种子）；
 * - 保存调 `PUT /api/settings`，成功用返回的全量值回填；非法值前端先拦
 *   （U14-6），绕过前端直达后端时由 client.ts 统一展示 10001 兜底文案。
 *
 * ⚠️ 数字输入框刻意不设 1~10 硬夹取（只给 0~99 宽界），否则 0/11 会被组件
 *    自动改成合法值，前端拦截分支永远走不到（验收 U14-6 要求能拦）。
 */
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import {
  COUNTDOWN_MAX,
  COUNTDOWN_MIN,
  SPEECH_RATE_MAX,
  SPEECH_RATE_MIN,
  getSettings,
  putSettings,
  type DictationHint,
} from '@/api/settings'

const loading = ref(false)
const loadedOnce = ref(false)
const saving = ref(false)

const form = reactive<{
  dictation_countdown_s: number
  spell_verdict_countdown_s: number
  dictation_hint: DictationHint
  dictation_speech_rate: number
}>({
  dictation_countdown_s: 3,
  spell_verdict_countdown_s: 3,
  dictation_hint: 'zh',
  dictation_speech_rate: 45,
})

/** 云端语音 API Key：后端只回掩码；apiKeyInput 是用户输入的新值（空=不改）。 */
const apiKeyMasked = ref('')
const apiKeyInput = ref('')
const clearingKey = ref(false)

const HINT_OPTIONS: { value: DictationHint; label: string; desc: string }[] = [
  { value: 'zh', label: '中文', desc: '报词时屏幕显示中文释义（背英文）' },
  { value: 'en', label: '英文', desc: '报词时屏幕显示英文（背中文）' },
]

function hintDesc(hint: DictationHint): string {
  return HINT_OPTIONS.find((opt) => opt.value === hint)?.desc ?? ''
}

/** 前端拦截（U14-6）：不合法直接提示并中止，不发请求。 */
function validateLocal(): string {
  for (const [label, value] of [
    ['听写倒计时', form.dictation_countdown_s],
    ['背诵判定倒计时', form.spell_verdict_countdown_s],
  ] as const) {
    if (!Number.isInteger(value) || value < COUNTDOWN_MIN || value > COUNTDOWN_MAX) {
      return `${label}取值必须为 ${COUNTDOWN_MIN}~${COUNTDOWN_MAX} 的整数`
    }
  }
  if (
    !Number.isInteger(form.dictation_speech_rate) ||
    form.dictation_speech_rate < SPEECH_RATE_MIN ||
    form.dictation_speech_rate > SPEECH_RATE_MAX
  ) {
    return `听写语速取值必须为 ${SPEECH_RATE_MIN}~${SPEECH_RATE_MAX} 的整数`
  }
  return ''
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await getSettings()
    form.dictation_countdown_s = data.dictation_countdown_s
    form.spell_verdict_countdown_s = data.spell_verdict_countdown_s
    form.dictation_hint = data.dictation_hint
    form.dictation_speech_rate = data.dictation_speech_rate
    apiKeyMasked.value = data.cloud_api_key ?? ''
    loadedOnce.value = true
  } catch {
    // 统一提示（client.ts 已弹）
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  const invalid = validateLocal()
  if (invalid) {
    ElMessage.warning(invalid)
    return
  }
  const newKey = apiKeyInput.value.trim()
  if (newKey.length > 200 || /[\r\n]/.test(newKey)) {
    ElMessage.warning('API Key 不能超过 200 字符且不能含换行')
    return
  }
  if (saving.value) return

  saving.value = true
  try {
    const payload: Parameters<typeof putSettings>[0] = {
      dictation_countdown_s: form.dictation_countdown_s,
      spell_verdict_countdown_s: form.spell_verdict_countdown_s,
      dictation_hint: form.dictation_hint,
      dictation_speech_rate: form.dictation_speech_rate,
    }
    // 输入留空 = 不修改 Key；填了 = 覆盖（后端保存即生效）
    if (newKey) payload.cloud_api_key = newKey
    const data = await putSettings(payload)
    // PUT 契约返回全量（同 GET），以后端值为准回填
    form.dictation_countdown_s = data.dictation_countdown_s
    form.spell_verdict_countdown_s = data.spell_verdict_countdown_s
    form.dictation_hint = data.dictation_hint
    form.dictation_speech_rate = data.dictation_speech_rate
    apiKeyMasked.value = data.cloud_api_key ?? ''
    apiKeyInput.value = ''
    ElMessage.success('已保存，修改即时生效')
  } catch {
    // 后端 10001 兜底等 → client.ts 统一展示错误文案
  } finally {
    saving.value = false
  }
}

/** 清除已配置的 Key（= PUT 空串，回落 asr.yaml 部署种子）。 */
async function clearKey(): Promise<void> {
  if (clearingKey.value) return
  clearingKey.value = true
  try {
    const data = await putSettings({ cloud_api_key: '' })
    apiKeyMasked.value = data.cloud_api_key ?? ''
    apiKeyInput.value = ''
    ElMessage.success('已清除，回落部署配置（asr.yaml）')
  } catch {
    // 统一提示
  } finally {
    clearingKey.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="练习设置" subtitle="听写 / 背诵的倒计时与报词显示语言，学生端只读" />

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6">
      <template v-if="loadedOnce">
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>倒计时</span>
              <span class="block-card__hint">取值 1~10 秒</span>
            </div>
          </template>

          <el-form label-width="140px" @submit.prevent>
            <el-form-item label="听写倒计时">
              <el-input-number
                v-model="form.dictation_countdown_s"
                :min="0"
                :max="99"
                :step="1"
              />
              <span class="form-hint">报词后 N 秒自动报下一个词</span>
            </el-form-item>

            <el-form-item label="背诵判定倒计时">
              <el-input-number
                v-model="form.spell_verdict_countdown_s"
                :min="0"
                :max="99"
                :step="1"
              />
              <span class="form-hint">判定出结果后 N 秒无操作自动下一题</span>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>报词显示语言</span>
              <span class="block-card__hint">听写时屏幕显示的词</span>
            </div>
          </template>

          <el-radio-group v-model="form.dictation_hint">
            <el-radio v-for="opt in HINT_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </el-radio>
          </el-radio-group>
          <div class="hint-desc">{{ hintDesc(form.dictation_hint) }}</div>
        </el-card>

        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>听写报词语速</span>
              <span class="block-card__hint">0~100，数字越大越快；默认 45（中速偏慢）</span>
            </div>
          </template>

          <el-form label-width="140px" @submit.prevent>
            <el-form-item label="报词语速">
              <el-slider
                v-model="form.dictation_speech_rate"
                :min="SPEECH_RATE_MIN"
                :max="SPEECH_RATE_MAX"
                :step="5"
                show-input
                class="rate-slider"
              />
            </el-form-item>
          </el-form>
          <div class="hint-desc">学生手机上按此语速报词；觉得太快调小、太慢调大，保存后学生下次进听写即生效</div>
        </el-card>

        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>云端语音 API Key</span>
              <span class="block-card__hint">百炼语音识别 / 合成 · 修改立即生效无需重启</span>
            </div>
          </template>

          <el-form label-width="140px" @submit.prevent>
            <el-form-item label="当前状态">
              <span class="key-state">
                {{ apiKeyMasked ? `已配置（${apiKeyMasked}）` : '未配置（使用部署配置）' }}
              </span>
              <el-button
                v-if="apiKeyMasked"
                link
                type="danger"
                :loading="clearingKey"
                class="key-clear"
                @click="clearKey"
              >
                清除
              </el-button>
            </el-form-item>
            <el-form-item label="新 API Key">
              <el-input
                v-model="apiKeyInput"
                type="password"
                show-password
                maxlength="200"
                placeholder="粘贴新 Key 后点「保存」立即生效；留空则不修改"
                class="key-input"
              />
            </el-form-item>
          </el-form>
        </el-card>

        <div class="save-bar">
          <el-button @click="load" :disabled="saving">重置</el-button>
          <el-button type="primary" :loading="saving" @click="save">
            {{ saving ? '保存中…' : '保存' }}
          </el-button>
        </div>
      </template>
    </LoadingBox>
  </div>
</template>

<style scoped>
.block-card {
  margin-bottom: 16px;
}

.block-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.block-card__hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--admin-text-secondary);
}

.form-hint {
  margin-left: 10px;
  font-size: 12px;
  color: var(--admin-text-secondary);
}

.hint-desc {
  margin-top: 8px;
  font-size: 13px;
  color: var(--admin-text-secondary);
}

.rate-slider {
  max-width: 460px;
}

.key-state {
  font-size: 13px;
  color: var(--admin-text-secondary);
}

.key-clear {
  margin-left: 12px;
}

.key-input {
  max-width: 420px;
}

.save-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}
</style>
