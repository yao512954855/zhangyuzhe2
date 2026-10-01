<script setup lang="ts">
/**
 * 服务地址与二维码 —— WP-WEB-03 F11 / F12，验收 U12 / U13
 *
 * F11：展示所有网卡地址、推荐地址高亮、当前 URL、**大尺寸二维码**（由 `qr_content` 渲染）；
 *      附「App 扫码即可连接」说明与 IP 变化提示。
 * F12：二维码组件 `ServerQRCard` 同时用于工作台首页与本页（同一组件，两处复用）。
 *
 * ⚠️ 二维码内容由**服务端**给出（`bjsys://{ip}:{port}`），管理端只负责渲染成图，
 *    不自己拼协议串 —— 拼错了 App 扫不出来，而且改协议要动两侧。
 *
 * ⚠️ U13（手机真扫码唤起 App）**无法在无头浏览器里自动化**：既没有真手机，
 *    也无法验证 App 的 URL scheme 注册。烟测里对 U13 的处理是：断言 `qr_content`
 *    形如 `bjsys://<ip>:<port>`，并断言二维码 canvas 真的渲染出了非空内容 + 尺寸 ≥240。
 *    真机扫码由人工验收，脚本里显式标 SKIP 并说明原因（不假装覆盖）。
 */
import { computed, onMounted, ref } from 'vue'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import ServerQRCard from '@/components/ServerQRCard.vue'
import { getServerInfo, ping, type NetInterface, type ServerInfo } from '@/api/server'
import { formatDateTime } from '@/utils/format'

const loading = ref(false)
const loadedOnce = ref(false)
const info = ref<ServerInfo | null>(null)
const pingMs = ref<number | null>(null)
const pingOk = ref<boolean | null>(null)

const KIND_LABEL: Record<string, string> = {
  ethernet: '有线',
  wifi: '无线',
  loopback: '本机回环',
  virtual: '虚拟网卡',
  vpn: 'VPN',
  unknown: '其他',
}

const qrCardRef = ref<InstanceType<typeof ServerQRCard> | null>(null)

async function load(refresh = false): Promise<void> {
  loading.value = true
  try {
    info.value = await getServerInfo(refresh)
    loadedOnce.value = true
    // 强刷探测后同步二维码卡片（卡片 autoLoad 自拉首帧，此后跟随本页刷新）
    if (refresh) qrCardRef.value?.refresh()
  } catch {
    // 统一提示
  } finally {
    loading.value = false
  }
}

async function doPing(): Promise<void> {
  const t0 = performance.now()
  try {
    await ping()
    pingMs.value = Math.round(performance.now() - t0)
    pingOk.value = true
  } catch {
    pingOk.value = false
    pingMs.value = null
  }
}

/** 推荐地址（服务端 `recommended=true`），没有就退回 `current_ip`。 */
const recommended = computed<NetInterface | null>(() => {
  const list = info.value?.interfaces ?? []
  return list.find((i) => i.recommended) ?? null
})

/** 手动输入用的地址（二维码扫不出来时的兜底，F11 硬要求）。 */
const manualUrl = computed(() => info.value?.current_url || '—')

/** 只展示 IPv4 且非回环的地址给「给学生的连接地址」用。 */
const clientFacing = computed(() =>
  (info.value?.interfaces ?? []).filter((i) => i.ip && i.ip !== '127.0.0.1'),
)

const ipChanged = computed(() => !!info.value?.last_ip_change_at)

/**
 * 降级项文案。
 *
 * ⚠️ 不要在模板里写多行箭头函数 —— `vue-tsc` 的模板解析器会把它当语句解析并报
 * `TS1005: ',' expected`（本次真踩过）。凡是需要 `map`/`filter` 的展示逻辑，
 * 一律先算成 computed 再绑定。
 */
const degradedText = computed(() => {
  const list = info.value?.degraded_reasons ?? []
  if (list.length === 0) return ''
  const kinds = list.map((r) =>
    r && typeof r === 'object' && 'kind' in r
      ? String((r as { kind: unknown }).kind)
      : String(r),
  )
  return `检测到 ${list.length} 项服务降级：${kinds.join('、')}`
})

onMounted(() => {
  load()
  doPing()
})
</script>

<template>
  <div class="admin-page">
    <PageHeader
      title="服务地址"
      :subtitle="info ? `${info.server_name} · ${info.service} ${info.version}` : 'App 连接信息'"
    >
      <el-button :loading="loading" @click="doPing">测连通</el-button>
      <el-button type="primary" :loading="loading" @click="load(true)">重新探测网卡</el-button>
    </PageHeader>

    <el-alert
      v-if="ipChanged"
      type="warning"
      :closable="false"
      class="calibre-tip"
      show-icon
    >
      <template #title>
        检测到本机地址近期发生过变化（{{ formatDateTime(info?.last_ip_change_at) }}）。
        换网络或换位置后地址会变，App 会自动发现服务；若学生连不上，请让他们重新扫码，
        或在 App 里手动输入下方的当前地址。
      </template>
    </el-alert>

    <LoadingBox :loading="loading" :has-data="loadedOnce" :rows="8">
      <template v-if="info">
        <!-- F11 / F12：大二维码卡片（同一组件也用在首页工作台） -->
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>扫码连接</span>
              <span class="block-card__hint">App 扫码即可连接；识别失败可手动输入地址</span>
            </div>
          </template>
          <!-- 2026-09-30 修：原来 auto-load=false 却没人喂数据 → 卡片全空白。
               现在卡片自拉首帧，本页「重新探测网卡」后经 ref 联动 refresh。 -->
          <ServerQRCard ref="qrCardRef" :size="280" />
          <el-alert
            type="info"
            :closable="false"
            class="calibre-tip inner-tip"
            :title="`二维码内容为 ${info.qr_content || '（暂无可用地址）'}；手动输入地址：${manualUrl}`"
          />
          <!-- F11：推荐地址高亮（服务端挑的「学生最可能连得上」的那个） -->
          <div v-if="recommended" class="recommend-line">
            <el-tag type="success" size="small">推荐</el-tag>
            <span class="recommend-line__ip">{{ recommended.ip }}</span>
            <span class="recommend-line__hint">
              （{{ recommended.name }}{{ recommended.description ? ' · ' + recommended.description : '' }}）
            </span>
          </div>
        </el-card>

        <!-- 网卡列表 -->
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>本机网卡地址</span>
              <span class="block-card__hint">共 {{ info.interfaces.length }} 个地址，推荐地址已标记</span>
            </div>
          </template>
          <el-table :data="info.interfaces" border stripe size="small" empty-text="没有探测到可用网卡">
            <el-table-column label="推荐" width="80" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.recommended" type="success" size="small">推荐</el-tag>
                <span v-else class="dim">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="ip" label="IP 地址" width="150">
              <template #default="{ row }">
                <span :class="{ 'ip-strong': row.recommended }">{{ row.ip }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="name" label="网卡" min-width="150" />
            <el-table-column label="类型" width="100" align="center">
              <template #default="{ row }">{{ KIND_LABEL[row.kind] ?? row.kind }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.is_up ? 'success' : 'info'" size="small">
                  {{ row.is_up ? '已连接' : '未连接' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="MAC" width="160">
              <template #default="{ row }">{{ row.mac || '—' }}</template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="160">
              <template #default="{ row }">{{ row.description || '—' }}</template>
            </el-table-column>
          </el-table>

          <el-alert
            v-if="clientFacing.length > 1"
            type="info"
            :closable="false"
            class="calibre-tip inner-tip"
            title="检测到多个可用地址：学生和服务器要在同一个局域网（同一个路由器/同一个 Wi-Fi）才能连上。若连不上，优先用「推荐」那一个。"
          />
        </el-card>

        <!-- 运行信息 -->
        <el-card shadow="never" class="block-card">
          <template #header>
            <div class="block-card__header">
              <span>运行信息</span>
              <span class="block-card__hint">
                探活{{ pingOk === null ? '未测' : pingOk ? `正常（${pingMs} ms）` : '失败' }}
              </span>
            </div>
          </template>
          <el-descriptions :column="3" border size="small">
            <el-descriptions-item label="服务标识">{{ info.service }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ info.version }}</el-descriptions-item>
            <el-descriptions-item label="服务名">{{ info.server_name }}</el-descriptions-item>
            <el-descriptions-item label="管理端端口">{{ info.app_port }}</el-descriptions-item>
            <el-descriptions-item label="发现端口（UDP）">{{ info.discovery_port }}</el-descriptions-item>
            <el-descriptions-item label="广播发现">
              {{ info.broadcast_enabled ? '已启用' : '未启用' }}
            </el-descriptions-item>
            <el-descriptions-item label="当前地址">{{ info.current_ip || '—' }}</el-descriptions-item>
            <el-descriptions-item label="当前 URL">{{ info.current_url || '—' }}</el-descriptions-item>
            <el-descriptions-item label="地址变更时间">
              {{ formatDateTime(info.last_ip_change_at) }}
            </el-descriptions-item>
            <el-descriptions-item label="历史地址" :span="3">
              <span v-if="info.previous_ips?.length">{{ info.previous_ips.join('、') }}</span>
              <span v-else class="dim">无</span>
            </el-descriptions-item>
          </el-descriptions>

          <el-alert
            v-if="info.degraded_reasons?.length"
            type="warning"
            :closable="false"
            class="calibre-tip inner-tip"
            :title="degradedText"
          />
        </el-card>
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
.calibre-tip {
  margin-bottom: 12px;
}
.inner-tip {
  margin-top: 12px;
  margin-bottom: 0;
}
.dim {
  color: var(--admin-text-secondary);
}
.ip-strong {
  font-weight: 600;
  color: var(--admin-brand);
}
.recommend-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  font-size: 13px;
}
.recommend-line__ip {
  font-weight: 600;
  color: var(--admin-brand);
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.recommend-line__hint {
  color: var(--admin-text-secondary);
  font-size: 12px;
}
</style>
