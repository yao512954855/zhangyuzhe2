<script setup lang="ts">
/**
 * 趋势折线图 —— WP-WEB-03（F7 / F13，ECharts）。
 *
 * 展示某日期范围内的：活跃学生数（左轴）、答题数（左轴，次）、正确率（右轴，百分比）。
 * 中文标签 + 数字带单位/百分比（F13）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
// ⚠️ 按需引入 ECharts：`import * as echarts from 'echarts'` 会把**整个库**打进产物
//（实测该路由 chunk 直接从 ~10KB 涨到 1MB）。这里只注册折线图 + 用到的组件，
// 产物体积回到几十 KB 量级。新增图表类型时记得在下面 `echarts.use([...])` 里补。
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

import type { TrendPoint } from '@/api/stats'

const props = defineProps<{ trend: TrendPoint[] }>()

const elRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function buildOption(): EChartsOption {
  const points = props.trend || []
  const dates = points.map((p) => p.date)
  const active = points.map((p) => p.active_students)
  const items = points.map((p) => p.item_count)
  const accuracy = points.map((p) => Number((p.accuracy * 100).toFixed(2)))

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const arr = params as Array<{ axisValue: string; seriesName: string; data: number }>
        if (!arr.length) return ''
        const lines = arr.map((s) => {
          const unit = s.seriesName === '正确率' ? '%' : '人/次'
          const val = s.seriesName === '正确率' ? `${s.data}%` : `${s.data}`
          return `${s.seriesName}：${val}${unit === '%' ? '' : ''}`
        })
        return `${arr[0].axisValue}<br/>${lines.join('<br/>')}`
      },
    },
    legend: {
      data: ['活跃学生数', '答题数', '正确率'],
      bottom: 0,
    },
    grid: { left: 48, right: 56, top: 24, bottom: 40 },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: { fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value',
        name: '人数/次数',
        axisLabel: { fontSize: 11 },
      },
      {
        type: 'value',
        name: '正确率',
        min: 0,
        max: 100,
        axisLabel: { formatter: '{value}%', fontSize: 11 },
      },
    ],
    series: [
      {
        name: '活跃学生数',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: active,
        itemStyle: { color: '#409eff' },
      },
      {
        name: '答题数',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: items,
        itemStyle: { color: '#67c23a' },
      },
      {
        name: '正确率',
        type: 'line',
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: accuracy,
        itemStyle: { color: '#e6a23c' },
      },
    ],
  }
}

function render(): void {
  if (!chart) return
  chart.setOption(buildOption(), true)
}

function resize(): void {
  chart?.resize()
}

onMounted(() => {
  if (elRef.value) {
    chart = echarts.init(elRef.value)
    render()
    window.addEventListener('resize', resize)
  }
})

watch(() => props.trend, render, { deep: false })

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="elRef" class="trend-chart" />
</template>

<style scoped>
.trend-chart {
  width: 100%;
  height: 320px;
}
</style>
