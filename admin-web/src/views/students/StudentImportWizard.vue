<script setup lang="ts">
/**
 * 学生导入向导 —— WP-WEB-03 F3 / F14
 *
 * F3：两步式 —— ① 上传校验（下载模板 → 选文件 → preview）② 预览（错误行 + 重复行 + 默认密码）
 *      ③ 确认入库 → 结果页提供「账号-初始密码对照表」下载入口。
 * 错误提示具体到行（后端 `invalid_rows[].reason` 已带行号与原因，如「学号重复」）。
 * 刷新可恢复（sessionStorage 存 preview_token 与预览数据，避免误刷新重传文件）。
 * 防假绿：预览阶段**真取错误报告 Blob** 校验可下载（见烟测 W 系列）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import PageHeader from '@/components/PageHeader.vue'
import LoadingBox from '@/components/LoadingBox.vue'
import {
  commitStudentImport,
  downloadCredentials,
  downloadImportErrorReport,
  downloadStudentTemplate,
  previewStudentImport,
  type StudentImportCommitResult,
  type StudentImportPreview,

} from '@/api/students'

const router = useRouter()

const STORAGE_KEY = 'web03_student_import_preview'

const activeStep = ref(0)
const uploading = ref(false)
const committing = ref(false)
const preview = ref<StudentImportPreview | null>(null)
const previewToken = ref('')
const errorReportUrl = ref('')

const form = reactive<{ defaultPassword: string }>({ defaultPassword: '' })

const commitResult = ref<StudentImportCommitResult | null>(null)

function restore(): void {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const data = JSON.parse(raw) as StudentImportPreview
    preview.value = data
    previewToken.value = data.preview_token
    errorReportUrl.value = data.error_report_url
    activeStep.value = 1
  } catch {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}

async function onFileSelected(file: File): Promise<void> {
  uploading.value = true
  try {
    const data = await previewStudentImport(file)
    preview.value = data
    previewToken.value = data.preview_token
    errorReportUrl.value = data.error_report_url
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    activeStep.value = 1
  } catch {
    // 40001/40002 等由 client 统一提示
  } finally {
    uploading.value = false
  }
}

function onFileInput(e: Event): void {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void onFileSelected(file)
}

function triggerUpload(): void {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.xlsx,.xls'
  input.onchange = onFileInput
  input.click()
}

async function downloadTemplate(): Promise<void> {
  try {
    const blob = await downloadStudentTemplate()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '学生名单导入模板.xlsx'
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    // 统一提示
  }
}

async function downloadErrors(): Promise<void> {
  if (!errorReportUrl.value) return
  try {
    const blob = await downloadImportErrorReport(errorReportUrl.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '导入错误明细.xlsx'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已下载错误报告')
  } catch {
    // 统一提示
  }
}

async function doCommit(): Promise<void> {
  committing.value = true
  try {
    const res = await commitStudentImport(previewToken.value, form.defaultPassword || undefined)
    commitResult.value = res
    sessionStorage.removeItem(STORAGE_KEY)
    activeStep.value = 2
  } catch {
    // 统一提示
  } finally {
    committing.value = false
  }
}

async function downloadCredentialsFile(): Promise<void> {
  if (!commitResult.value) return
  try {
    const blob = await downloadCredentials(commitResult.value.credentials_url)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '学生账号初始密码对照表.xlsx'
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已下载账号-初始密码对照表')
  } catch {
    // 统一提示
  }
}

function restart(): void {
  preview.value = null
  previewToken.value = ''
  errorReportUrl.value = ''
  commitResult.value = null
  form.defaultPassword = ''
  activeStep.value = 0
  sessionStorage.removeItem(STORAGE_KEY)
}

function goStudents(): void {
  router.push('/students/list')
}

onMounted(restore)
</script>

<template>
  <div class="admin-page">
    <PageHeader title="学生导入" subtitle="下载模板 → 上传名单 → 预览校验 → 确认入库">
      <el-button @click="router.push('/students/list')">返回学生列表</el-button>
    </PageHeader>

    <el-steps :active="activeStep" align-center finish-status="success" class="wizard-steps">
      <el-step title="上传名单" />
      <el-step title="预览校验" />
      <el-step title="确认入库" />
    </el-steps>

    <LoadingBox :loading="uploading" :has-data="preview !== null" :rows="6">
      <!-- 步骤 1：上传 -->
      <el-card v-if="activeStep === 0" shadow="never" class="step-card">
        <div class="upload-area">
          <p>请先下载模板，按模板填写「学号 / 姓名 / 班级 / 初始密码」后上传。</p>
          <el-button type="primary" @click="downloadTemplate">下载导入模板</el-button>
          <el-button :loading="uploading" @click="triggerUpload">选择文件上传</el-button>
          <p class="upload-tip">仅支持 .xlsx；单次最多导入 1000 行；班级需在「班级管理」中先创建。</p>
        </div>
      </el-card>

      <!-- 步骤 2：预览校验 -->
      <el-card v-else-if="activeStep === 1 && preview" shadow="never" class="step-card">
        <el-alert type="info" :closable="false" class="summary-alert">
          <template #title>
            共 {{ preview.summary.total }} 行：有效 {{ preview.summary.valid }} 行，非法
            {{ preview.summary.invalid }} 行，与已有账号重复 {{ preview.summary.duplicated }} 行
          </template>
        </el-alert>

        <el-form label-width="100px" class="pwd-form">
          <el-form-item label="默认初始密码">
            <el-input
              v-model="form.defaultPassword"
              placeholder="留空则使用系统默认初始密码（123456）"
              maxlength="20"
              style="max-width: 360px"
            />
            <span class="pwd-hint">仅作用于新建账号；已存在账号不会重置密码（除非该行显式填了密码）。</span>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">错误行（不会入库）</el-divider>
        <el-table v-if="preview.invalid_rows.length" :data="preview.invalid_rows" border size="small" max-height="280">
          <el-table-column prop="row" label="行号" width="80" align="center" />
          <el-table-column prop="reason" label="原因" min-width="300" />
        </el-table>
        <el-empty v-else description="无错误行" :image-size="60" />

        <el-divider content-position="left">重复行（已存在账号，将更新）</el-divider>
        <el-table v-if="preview.duplicated_rows.length" :data="preview.duplicated_rows" border size="small" max-height="200">
          <el-table-column prop="row" label="行号" width="80" align="center" />
          <el-table-column prop="username" label="学号" width="160" />
          <el-table-column label="处理方式" min-width="160">
            <template #default="{ row }">{{ row.action === 'update' ? '更新已有账号' : '新建' }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="无重复行" :image-size="60" />

        <div class="step-actions">
          <el-button @click="restart">重新上传</el-button>
          <el-button v-if="preview.invalid_rows.length" link type="primary" @click="downloadErrors">
            下载错误报告
          </el-button>
          <el-button type="primary" :loading="committing" @click="doCommit">确认入库</el-button>
        </div>
      </el-card>

      <!-- 步骤 3：结果 -->
      <el-card v-else-if="activeStep === 2 && commitResult" shadow="never" class="step-card">
        <el-result icon="success" title="导入完成" sub-title="学生名单已入库">
          <template #extra>
            <div class="result-stats">
              <div><b>{{ commitResult.created }}</b><span>新建</span></div>
              <div><b>{{ commitResult.updated }}</b><span>更新</span></div>
              <div><b>{{ commitResult.skipped }}</b><span>跳过（非法行）</span></div>
            </div>
            <div class="result-actions">
              <el-button type="primary" @click="downloadCredentialsFile">下载账号-初始密码对照表</el-button>
              <el-button @click="goStudents">查看学生列表</el-button>
              <el-button @click="restart">继续导入</el-button>
            </div>
            <p class="result-tip">
              对照表含初始明文密码（bcrypt 后续不可反推），请妥善分发；若未设置默认密码，默认初始密码为 123456。
            </p>
          </template>
        </el-result>
      </el-card>
    </LoadingBox>
  </div>
</template>

<style scoped>
.wizard-steps {
  margin: 8px 0 20px;
}
.step-card {
  background: var(--admin-panel);
}
.upload-area {
  text-align: center;
  padding: 24px 0;
}
.upload-area p {
  margin: 12px 0;
  color: var(--admin-text-secondary, #909399);
}
.upload-tip {
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}
.summary-alert {
  margin-bottom: 16px;
}
.pwd-form {
  margin: 8px 0 4px;
}
.pwd-hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}
.step-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}
.result-stats {
  display: flex;
  justify-content: center;
  gap: 32px;
  margin: 8px 0 16px;
}
.result-stats div {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.result-stats b {
  font-size: 24px;
}
.result-stats span {
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}
.result-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
}
.result-tip {
  margin-top: 16px;
  font-size: 12px;
  color: var(--admin-text-secondary, #909399);
}
</style>
