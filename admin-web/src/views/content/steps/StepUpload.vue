<script setup lang="ts">
/**
 * 导入向导 · 步骤 1~2 —— WP-WEB-02 F10/F11/F15
 *
 * 步骤 1：选类型（英语词条 / 语文课文）+ 下载模板。
 * 步骤 2：拖拽或点击上传；显示解析中；后端返回 preview 后上抛给向导。
 * F15：非 xlsx 先在前端拦截并提示（后端 40001 为兜底）；40002/40004 由 catch 给出引导。
 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { UploadFilled, Download } from '@element-plus/icons-vue'

import { previewTexts, previewWords, type ImportType, type TextsPreview, type WordsPreview } from '@/api/imports'
import { downloadTemplate } from '@/api/imports'
import { BizError } from '@/api/client'

const props = defineProps<{ type: ImportType | '' }>()
const emit = defineEmits<{
  'update:type': [ImportType]
  previewed: [{ type: ImportType; data: WordsPreview | TextsPreview }]
  stage: [number]
}>()

const subStep = ref<1 | 2>(1)
const uploading = ref(false)
const fileRef = ref<File | null>(null)

const typeOptions: { value: ImportType; label: string; desc: string }[] = [
  { value: 'words', label: '英语词条', desc: '单词 / 短语 / 句子，含可接受答案、音标、是否拼读' },
  { value: 'texts', label: '语文课文', desc: '课文正文 + 段落，导入后勾选背诵范围' },
]

function chooseType(t: ImportType) {
  emit('update:type', t)
  subStep.value = 2
  emit('stage', 2)
}

async function downloadTpl() {
  if (!props.type) {
    ElMessage.warning('请先选择导入类型')
    return
  }
  try {
    const blob = await downloadTemplate(props.type)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = props.type === 'words' ? '英语词条导入模板.xlsx' : '语文课文导入模板.xlsx'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch {
    // client 统一提示
  }
}

function onFileSelected(file: File) {
  // F15：前端先拦非 xlsx（后端 40001 兜底）
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    ElMessage.error('文件格式错误：请上传 .xlsx 文件（非 xlsx）')
    return
  }
  fileRef.value = file
  void doUpload(file)
}

async function doUpload(file: File) {
  uploading.value = true
  try {
    const data = props.type === 'words' ? await previewWords(file) : await previewTexts(file)
    emit('previewed', { type: props.type as ImportType, data })
    ElMessage.success('解析完成，请核对预览')
  } catch (e) {
    if (e instanceof BizError) {
      if (e.code === 40002) {
        await ElMessageBox.alert('模板列缺失或表头不匹配。请下载最新模板，按模板列名填写后重试。', '模板列缺失', {
          type: 'warning',
          confirmButtonText: '下载最新模板',
        }).then(downloadTpl).catch(() => {})
      } else if (e.code === 40004) {
        ElMessage.error('文件过大（上限 10MB）。请拆分为多个较小的文件分批导入。')
      } else if (e.code === 40001) {
        ElMessage.error('文件格式错误：请上传 .xlsx 文件。')
      } else if (e.code === 40003) {
        ElMessage.error(e.message || '数据校验失败，请查看预览中的错误明细')
      }
      // 其它业务码已由 client 统一弹
    }
  } finally {
    uploading.value = false
  }
}

function resetFile() {
  fileRef.value = null
}
</script>

<template>
  <div class="step-upload">
    <!-- 步骤 1：选类型 + 下载模板 -->
    <div v-show="subStep === 1 || true" class="upload-block">
      <h4 class="block-title">① 选择导入类型并下载模板</h4>
      <div class="type-cards">
        <div
          v-for="o in typeOptions"
          :key="o.value"
          class="type-card"
          :class="{ active: props.type === o.value }"
          @click="chooseType(o.value)"
        >
          <div class="type-card__label">{{ o.label }}</div>
          <div class="type-card__desc">{{ o.desc }}</div>
          <el-icon v-if="props.type === o.value" class="type-card__check"><Download /></el-icon>
        </div>
      </div>
      <el-button :disabled="!props.type" :icon="Download" @click="downloadTpl">下载 {{ props.type === 'words' ? '词条' : '课文' }}模板</el-button>
    </div>

    <!-- 步骤 2：上传 -->
    <div class="upload-block">
      <h4 class="block-title">② 上传并校验</h4>
      <el-upload
        drag
        :auto-upload="false"
        :show-file-list="false"
        accept=".xlsx"
        :on-change="
          (uploadFile: UploadFile) => onFileSelected(uploadFile.raw as File)
        "
        :disabled="uploading"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">将文件拖到此处，或<em>点击上传</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 .xlsx；单次上限 10MB。已选：{{ fileRef?.name || '无' }}</div>
        </template>
      </el-upload>

      <el-progress
        v-if="uploading"
        :percentage="100"
        status="warning"
        :indeterminate="true"
        :duration="2"
        :show-text="false"
        class="upload-progress"
      />
      <div v-if="uploading" class="uploading-text">解析中，请稍候…</div>

      <div v-if="fileRef && !uploading" class="upload-actions">
        <el-button @click="resetFile">重新选择</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-upload {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.upload-block {
  border: 1px solid var(--admin-border);
  border-radius: 8px;
  padding: 16px;
}
.block-title {
  margin: 0 0 12px;
  font-size: 15px;
}
.type-cards {
  display: flex;
  gap: 16px;
  margin-bottom: 12px;
}
.type-card {
  position: relative;
  flex: 1;
  border: 1px solid var(--admin-border);
  border-radius: 8px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.15s;
}
.type-card:hover {
  border-color: var(--admin-brand);
}
.type-card.active {
  border-color: var(--admin-brand);
  background: rgb(from var(--admin-brand) r g b / 0.06);
}
.type-card__label {
  font-weight: 600;
  font-size: 15px;
}
.type-card__desc {
  margin-top: 6px;
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.type-card__check {
  position: absolute;
  top: 12px;
  right: 12px;
  color: var(--admin-brand);
}
.upload-progress {
  margin-top: 12px;
}
.uploading-text {
  margin-top: 8px;
  color: var(--admin-text-secondary);
  font-size: 13px;
}
.upload-actions {
  margin-top: 12px;
}
</style>
