<script setup lang="ts">
import { useRouter } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'

const router = useRouter()
</script>

<template>
  <div class="admin-page">
    <EmptyState description="页面不存在或已被移除">
      <el-button type="primary" @click="router.replace('/dashboard')">回到工作台</el-button>
    </EmptyState>
  </div>
</template>
