<script setup lang="ts">
/**
 * AI 能力模式切换 —— 批次 D（验收照 vue-admin-shell 套路）。
 *
 * 消费 `GET /api/capabilities` 与 `PUT /api/capabilities/mode`（批次 A 已上线）。
 *
 * 行为要点（工作包 §2）：
 * - 三档 radio 默认选中 = GET 返回的 `mode`；切换后**底部出现「保存」按钮**（防误触，不自动保存）；
 * - 保存调 `setMode`，成功 `ElMessage.success('已保存，立即生效')`，失败走 `client.ts` 的 BizError toast；
 * - 保存中按钮 loading + 防重复提交；
 * - 三行能力状态渲染（tts=听写播报 / ocr=拍照批改 / asr=背诵判分）。
 *
 * ⚠️ 不在模板里写多行箭头函数 —— `vue-tsc` 的模板解析器会报错（真踩过，见 ServerAddress.vue 注释）。
 *    凡是需要 map/filter 的展示逻辑，一律先算成 computed 再绑定。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import {
  getCapabilities,
  setMode,
  type Capabilities,
  type CapabilityMode,
  type EngineStatus,
} from '@/api/capabilities'

// ---------------------------------------------------------------------------
// 静态映射
// ---------------------------------------------------------------------------

interface ModeOption {
  value: CapabilityMode
  label: string
  desc: string
}

const MODE_OPTIONS: ModeOption[] = [
  { value: 'hybrid', label: '智能切换（推荐）', desc: '优先本地引擎，失败自动用云端' },
  { value: 'local', label: '纯本地', desc: '完全离线可用；本地引擎故障时报错' },
  { value: 'cloud', label: '纯云端', desc: '与联网环境表现一致' },
]

/** 能力中文名映射（块2 行首）。 */
const ENGINE_LABELS: Record<keyof Capabilities['engines'], string> = {
  tts: '听写播报',
  ocr: '拍照批改',
  asr: '背诵判分',
}

/** 引擎生效模式徽标颜色（local=info / hybrid=success / cloud=warning）。 */
const MODE_TAG_TYPE: Record<CapabilityMode, 'info' | 'success' | 'warning'> = {
  local: 'info',
  hybrid: 'success',
  cloud: 'warning',
}

const MODE_LABEL: Record<CapabilityMode, string> = {
  local: '纯本地',
  hybrid: '智能切换',
  cloud: '纯云端',
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------

const loading = ref(false)
const loadedOnce = ref(false)
const saving = ref(false)
const caps = ref<Capabilities | null>(null)

/** 当前选中的模式（radio 绑定）。 */
const selectedMode = ref<CapabilityMode>('hybrid')
/** 进入页面时 GET 返回的「已保存」模式，用于判断是否需要保存。 */
const savedMode = ref<CapabilityMode>('hybrid')

/** 与已保存值不一致 → 出现保存按钮。 */
const dirty = computed(() => selectedMode.value !== savedMode.value)

/** 块2 三行能力状态（按固定顺序 tts/ocr/asr）。 */
const engineRows = computed(() => {
  const engines = caps.value?.engines
  if (!engines) return []
  return (Object.keys(ENGINE_LABELS) as (keyof Capabilities['engines'])[]).map((key) => ({
    key,
    label: ENGINE_LABELS[key],
    status: engines[key],
  }))
})

// ---------------------------------------------------------------------------
// 行为
// ---------------------------------------------------------------------------

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await getCapabilities()
    caps.value = data
    savedMode.value = data.mode
    selectedMode.value = data.mode
    loadedOnce.value = true
  } catch {
    // 统一提示（client.ts 已弹）
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!dirty.value || saving.value) return
  saving.value = true
  try {
    const data = await setMode(selectedMode.value)
    caps.value = data
    savedMode.value = data.mode
    selectedMode.value = data.mode
    ElMessage.success('已保存，立即生效')
  } catch {
    // 统一提示（client.ts 已弹 BizError）
  } finally {
    saving.value = false
  }
}

function localReadyText(s: EngineStatus): string {
  return s.local_ready ? `本地引擎 ✅ ${s.local_detail}` : `⚠️ 本地引擎未就绪：${s.local_detail}`
}

onMounted(load)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="AI 能力模式" subtitle="本地 AI 能力开关（TTS / OCR / ASR）" />

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="6">
      <template v-if="caps">
        <!-- 块1 · 全局模式 -->
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>全局模式</span>
              <span class="block-card__hint">切换后需点「保存」才生效，防止误触</span>
            </div>
          </template>

          <el-radio-group v-model="selectedMode">
            <el-radio-button
              v-for="opt in MODE_OPTIONS"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </el-radio-button>
          </el-radio-group>

          <div class="mode-desc">
            <el-alert
              v-for="opt in MODE_OPTIONS"
              v-show="opt.value === selectedMode"
              :key="opt.value"
              type="info"
              :closable="false"
              class="mode-desc__alert"
              :title="opt.desc"
            />
          </div>
        </el-card>

        <!-- 块2 · 能力状态 -->
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>能力状态</span>
              <span class="block-card__hint">各能力实际生效模式与本地引擎就绪情况</span>
            </div>
          </template>

          <div v-for="row in engineRows" :key="row.key" class="engine-row">
            <div class="engine-row__head">
              <span class="engine-row__name">{{ row.label }}</span>
              <el-tag :type="MODE_TAG_TYPE[row.status.mode]" size="small">
                {{ MODE_LABEL[row.status.mode] }}
              </el-tag>
            </div>
            <div
              class="engine-row__local"
              :class="{ 'is-warn': !row.status.local_ready }"
            >
              {{ localReadyText(row.status) }}
            </div>
            <div v-if="row.status.locked" class="engine-row__lock">
              已被部署配置（.env）锁定，界面切换对该能力无效
            </div>
          </div>
        </el-card>

        <!-- 块3 · 说明文字 -->
        <el-alert
          type="info"
          :closable="false"
          class="block-card note"
          title="模式保存后立即生效，无需重启服务；正在进行的语音会话将在下一题应用新模式。"
        />

        <!-- 底部保存（仅在 dirty 时出现） -->
        <div v-if="dirty" class="save-bar">
          <span class="save-bar__hint">已修改，记得点保存</span>
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
.mode-desc {
  margin-top: 12px;
}
.mode-desc__alert {
  margin: 0;
}
.engine-row {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.engine-row:last-child {
  border-bottom: none;
}
.engine-row__head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.engine-row__name {
  font-weight: 600;
}
.engine-row__local {
  margin-top: 6px;
  font-size: 13px;
  color: var(--admin-text-secondary);
}
.engine-row__local.is-warn {
  color: var(--el-color-warning);
}
.engine-row__lock {
  margin-top: 4px;
  font-size: 12px;
  color: var(--admin-text-secondary);
}
.note {
  margin-bottom: 0;
}
.save-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 16px;
}
.save-bar__hint {
  font-size: 13px;
  color: var(--el-color-warning);
}
</style>
