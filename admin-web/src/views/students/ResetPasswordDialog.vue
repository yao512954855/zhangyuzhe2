<script setup lang="ts">
/**
 * 重置学生密码弹窗 —— WP-WEB-03 F5
 *
 * F5：弹窗输入新密码（6–20 位，允许纯数字）；成功后提示需告知学生。
 * 危险操作（重置密码）二次确认，文案说清后果（共通约束 #4）。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { resetStudentPassword, type StudentItem } from '@/api/students'

const props = defineProps<{ modelValue: boolean; student: StudentItem | null }>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; saved: [] }>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const formRef = ref()
const saving = ref(false)
const form = reactive<{ password: string }>({ password: '123456' })

const rules = {
  password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, max: 20, message: '密码长度 6–20 位', trigger: 'blur' },
  ],
}

// 每次打开重置为默认弱口令提示，避免残留上次输入
watch(visible, (v) => {
  if (v) form.password = '123456'
})

async function submit(): Promise<void> {
  await formRef.value.validate()
  const student = props.student
  if (!student) return
  try {
    await ElMessageBox.confirm(
      `确定将「${student.real_name}（学号 ${student.username}）」的密码重置为「${form.password}」？\n重置后该生需使用新密码登录，请务必单独告知本人。`,
      '确认重置密码',
      { type: 'warning', confirmButtonText: '确认重置', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  saving.value = true
  try {
    await resetStudentPassword(student.id, form.password)
    ElMessage.success(`已重置密码，请告知学生新密码：${form.password}`)
    visible.value = false
    emit('saved')
  } catch {
    // 统一提示
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="重置学生密码"
    width="440px"
    :close-on-click-modal="false"
  >
    <el-alert
      type="warning"
      :closable="false"
      title="重置后该生旧密码立即失效，需使用新密码登录，请单独告知学生本人。"
      style="margin-bottom: 12px"
    />
    <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
      <el-form-item label="学生">
        <span v-if="student">{{ student.real_name }}（学号 {{ student.username }}）</span>
        <span v-else>-</span>
      </el-form-item>
      <el-form-item label="新密码" prop="password">
        <el-input
          v-model="form.password"
          type="text"
          show-password
          placeholder="6–20 位，允许纯数字"
          maxlength="20"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">确认重置</el-button>
    </template>
  </el-dialog>
</template>
