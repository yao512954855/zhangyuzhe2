<script setup lang="ts">
/**
 * 服务地址二维码卡片 —— WP-WEB-03（F11 / F12）
 *
 * 复用于「工作台首页」与「服务地址页」（F12 要求同一组件两处复用）。
 * - 调用 `/api/server/info` 取 `qr_content`（`bjsys://{ip}:{port}`）与网卡列表；
 * - 用 `qrcode` 渲染 **≥240×240、高对比（黑底白字→实为黑点白底）** 的二维码；
 * - **同时给出文字版地址**：识别失败能手动输入（F11 硬要求）。
 */
import { onMounted, ref, watch } from 'vue'
import QRCode from 'qrcode'

import { getServerInfo, type NetInterface, type ServerInfo } from '@/api/server'

const props = withDefaults(defineProps<{ size?: number; autoLoad?: boolean }>(), {
  size: 240,
  autoLoad: true,
})

const loading = ref(false)
const info = ref<ServerInfo | null>(null)
const qrContent = ref('')
const error = ref('')

const canvasRef = ref<HTMLCanvasElement | null>(null)

async function renderQr(): Promise<void> {
  if (!canvasRef.value) return
  if (!qrContent.value) {
    const ctx = canvasRef.value.getContext('2d')
    if (ctx) ctx.clearRect(0, 0, props.size, props.size)
    return
  }
  try {
    await QRCode.toCanvas(canvasRef.value, qrContent.value, {
      width: props.size,
      margin: 1,
      // 高对比：纯黑点 + 纯白底，提高手机扫码成功率
      color: { dark: '#000000', light: '#ffffff' },
      errorCorrectionLevel: 'M',
    })
  } catch {
    // 渲染失败不阻断页面，文字版地址仍可手动输入
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await getServerInfo()
    info.value = data
    qrContent.value = data.qr_content || ''
  } catch {
    // client 已统一弹错误提示
    error.value = '服务地址获取失败'
  } finally {
    loading.value = false
  }
}

const recommendedIp = (): string => {
  const list: NetInterface[] = info.value?.interfaces || []
  const rec = list.find((i) => i.recommended)
  return rec?.ip || info.value?.current_ip || ''
}

function refresh(): void {
  load()
}

onMounted(() => {
  if (props.autoLoad) load()
})

// ⚠️ 必须监听 loading 且用 flush:'post'（2026-09-30 修「二维码空白」）：
// load() 里 qrContent 与 loading 在同一 tick 内先后变化，默认 flush:'pre' 的
// watcher 跑在**重渲染之前** —— 此时 skeleton 还在、canvas 未挂载，
// renderQr 拿到 null 直接 return，之后再没人画 → 用户看到一块空白画布。
// flush:'post' 保证回调跑在 DOM 更新之后，canvas 已挂载。
watch([qrContent, loading, () => props.size], () => {
  void renderQr()
}, { flush: 'post' })

// 供父页面联动刷新（如服务地址页「重新探测网卡」后同步卡片数据）。
// ⚠️ autoLoad=false 时组件不自拉数据，父页面必须主动调 refresh（2026-09-30 修）。
defineExpose({ refresh })
</script>

<template>
  <div class="server-qr-card">
    <div class="server-qr-card__qr">
      <el-skeleton v-if="loading" :rows="6" animated style="width: 240px" />
      <template v-else>
        <!-- F11：二维码 ≥240×240，并断言渲染出 canvas -->
        <canvas
          ref="canvasRef"
          :width="size"
          :height="size"
          class="server-qr-card__canvas"
        />
        <div v-if="!qrContent" class="server-qr-card__qr-empty">
          暂无可用网络地址，无法生成二维码
        </div>
      </template>
    </div>

    <div class="server-qr-card__meta">
      <div class="server-qr-card__row">
        <span class="server-qr-card__label">推荐地址</span>
        <span class="server-qr-card__value server-qr-card__value--strong">
          {{ recommendedIp() || '—' }}
        </span>
      </div>
      <div class="server-qr-card__row">
        <span class="server-qr-card__label">当前访问</span>
        <span class="server-qr-card__value">{{ info?.current_url || '—' }}</span>
      </div>
      <div class="server-qr-card__row">
        <span class="server-qr-card__label">二维码内容</span>
        <!-- F11：文字版地址，识别失败可手动输入 -->
        <span class="server-qr-card__value server-qr-card__mono">
          {{ qrContent || '—' }}
        </span>
      </div>
      <div class="server-qr-card__row">
        <span class="server-qr-card__label">服务名</span>
        <span class="server-qr-card__value">{{ info?.server_name || '背记系统' }}</span>
      </div>
      <div v-if="info?.degraded_reasons?.length" class="server-qr-card__warn">
        检测到降级：{{ info.degraded_reasons.length }} 项，详见服务地址页
      </div>
      <div class="server-qr-card__hint">App 扫码即可连接；识别失败可手动输入上方地址。</div>
      <el-button
        v-if="!autoLoad || true"
        size="small"
        :loading="loading"
        @click="refresh"
      >
        刷新
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.server-qr-card {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
  align-items: flex-start;
}
.server-qr-card__qr {
  flex: 0 0 auto;
}
.server-qr-card__canvas {
  display: block;
  border: 1px solid var(--admin-border, #ebeef5);
  border-radius: 6px;
  background: #fff;
}
.server-qr-card__qr-empty {
  width: 240px;
  height: 240px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--admin-border, #ebeef5);
  border-radius: 6px;
  color: var(--admin-text-secondary, #909399);
  font-size: 13px;
  text-align: center;
  padding: 12px;
  box-sizing: border-box;
}
.server-qr-card__meta {
  flex: 1 1 240px;
  min-width: 220px;
}
.server-qr-card__row {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  line-height: 1.6;
}
.server-qr-card__label {
  flex: 0 0 72px;
  color: var(--admin-text-secondary, #909399);
}
.server-qr-card__value {
  flex: 1 1 auto;
  word-break: break-all;
}
.server-qr-card__value--strong {
  font-weight: 600;
  font-size: 15px;
}
.server-qr-card__mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.server-qr-card__warn {
  margin-top: 8px;
  color: #e6a23c;
  font-size: 12px;
}
.server-qr-card__hint {
  margin-top: 8px;
  color: var(--admin-text-secondary, #909399);
  font-size: 12px;
}
</style>
